import json
import pytest
import subprocess

from urban_tree_ml.feedback import persist_review_payload, load_persisted_reviews, ReviewStateConflictError
from urban_tree_ml.review_gallery import apply_scene_patch, read_collection, scene_state, gallery_page


def setup(tmp_path):
    samples = [dict(sample_id=s, tree_id=t, scene_id=s, longitude=lon, latitude=42.3,
                    target_x=50, target_y=50, transform_a=.6, transform_b=0,
                    transform_d=0, transform_e=-.6, split='train')
               for s,t,lon in [('a','shared',-71.1),('b','shared',-71.1),('c','other',-71.2)]]
    manifest = {'metadata':{'review_id':'fixture','curation_schema_version':2,'curation_crs':'EPSG:26919'},
                'samples':samples, 'scenes':[dict(scene_id=s['sample_id'], sample_ids=[s['sample_id']],
                    image_width=100,image_height=100,splits=['train'],tree_count=1,image='x.png') for s in samples]}
    (tmp_path/'manifest.json').write_text(json.dumps(manifest))
    persist_review_payload(tmp_path, {'reviews':{'a':{'status':'aligned'},'c':{'status':'uncertain'}},
        'scene_reviews':{'c':{'done':True}},
        'mask_regions':[dict(region_id=id,longitude=lon,latitude=42.3,radius_m=5,mode='protect')
                        for id,lon in [('near',-71.1),('far',-71.2)]]})
    return read_collection(tmp_path)


def test_scoped_load_and_merge_preserve_other_trees_and_masks(tmp_path):
    manifest, state = setup(tmp_path)
    scoped = scene_state(manifest,state,'a')
    assert set(scoped['reviews']) == {'a'}
    assert [r['region_id'] for r in scoped['mask_regions']] == ['near']
    apply_scene_patch(tmp_path,'a',{'base_revision':state['state_revision'],
        'reviews':{'a':{'status':'offset','east_m':6,'north_m':-2}},'delete_mask_regions':['near']})
    result = load_persisted_reviews(tmp_path)
    assert result['reviews']['a'] == result['reviews']['b']
    assert result['reviews']['a']['east_m'] == pytest.approx(6, abs=.0001)
    assert result['reviews']['c'] == state['reviews']['c']
    assert result['scene_reviews'] == state['scene_reviews']
    assert [r['region_id'] for r in result['mask_regions']] == ['far']
    with pytest.raises(ReviewStateConflictError):
        apply_scene_patch(tmp_path,'a',{'base_revision':state['state_revision']})


def test_fast_save_cache_matches_disk_and_preserves_prior_snapshot(tmp_path, monkeypatch):
    _, original = setup(tmp_path)
    before=json.dumps(original,sort_keys=True)
    state=original
    for i in range(3):
        result=apply_scene_patch(tmp_path,'a',{'base_revision':state['state_revision'],
            'reviews':{'a':{'status':'offset','east_m':i*.6,'north_m':-1.2,'crown_radius_m':7+i}}})
        _, state=read_collection(tmp_path)
        disk=load_persisted_reviews(tmp_path)
        assert result['state_revision']==disk['state_revision']==state['state_revision']
        assert state['reviews']==disk['reviews']
        assert state['mask_regions']==disk['mask_regions']
    assert json.dumps(original,sort_keys=True)==before
    # A warm patch should not re-enter the full JSON read/normalization path.
    import urban_tree_ml.review_gallery as module
    monkeypatch.setattr(module,'load_persisted_reviews',lambda *a,**k:pytest.fail('full reload'))
    apply_scene_patch(tmp_path,'a',{'base_revision':state['state_revision'],'reviews':{'a':{'status':'aligned'}}})


def test_external_write_invalidates_cache_and_failed_write_does_not_prime(tmp_path,monkeypatch):
    _, old=setup(tmp_path)
    persist_review_payload(tmp_path,{**old,'reviews':{**old['reviews'],'c':{'status':'not-tree'}}})
    with pytest.raises(ReviewStateConflictError):
        apply_scene_patch(tmp_path,'a',{'base_revision':old['state_revision']})
    _, state=read_collection(tmp_path)
    before=(tmp_path/'reviews.json').read_bytes()
    import urban_tree_ml.review_gallery as module
    def fail(*a): raise OSError('disk unavailable')
    monkeypatch.setattr(module,'_write_json_atomic',fail)
    with pytest.raises(OSError):
        apply_scene_patch(tmp_path,'a',{'base_revision':state['state_revision'],'reviews':{'a':{'status':'not-tree'}}})
    assert (tmp_path/'reviews.json').read_bytes()==before
    assert read_collection(tmp_path)[1]['state_revision']==state['state_revision']


@pytest.mark.parametrize('patch', [{'reviews':{'c':{'status':'not-tree'}}},
    {'delete_mask_regions':['far']}, {'delete_scene_reviews':['c']}])
def test_cannot_modify_records_outside_loaded_chip(tmp_path,patch):
    _, state=setup(tmp_path)
    before=(tmp_path/'reviews.json').read_bytes()
    with pytest.raises(ValueError):
        apply_scene_patch(tmp_path,'a',{'base_revision':state['state_revision'],**patch})
    assert (tmp_path/'reviews.json').read_bytes()==before


def test_gallery_contains_only_summaries_and_filters_without_loading_editor(tmp_path):
    manifest,state=setup(tmp_path)
    page=gallery_page(manifest,state,{'status':['pending']})
    assert page['total']==2 and page['queue']==['a','b']
    assert 'reviews' not in page and 'samples' not in json.dumps(page)
    page=gallery_page(manifest,state,{'status':['done']})
    assert page['queue']==['c']


def test_client_delta_sends_changes_and_explicit_deletions_only():
    from urban_tree_ml.quality import _render_grouped_registration_html
    html=_render_grouped_registration_html([],{},[])
    start=html.index('function reviewDelta(')
    end=html.index('let editVersion=',start)
    script=html[start:end]+'''
const baseline={reviews:{a:{note:'keep',status:'aligned'},b:{status:'aligned'}},scene_reviews:{s:{done:true}},mask_regions:[{region_id:'m',mode:'protect'}]};
const current={reviews:{a:{status:'aligned',note:'keep'},b:{status:'uncertain'}},scene_reviews:{},mask_regions:[]};
const delta=reviewDelta(current,baseline);
if(JSON.stringify(Object.keys(delta.reviews))!=='["b"]')throw Error('unchanged record sent');
if(delta.delete_scene_reviews[0]!=='s'||delta.delete_mask_regions[0]!=='m')throw Error('missing deletion');
if(Object.keys(reviewDelta(baseline,baseline).reviews).length)throw Error('not idempotent');
'''
    subprocess.run(['node','-e',script],check=True,capture_output=True,encoding='utf-8')
