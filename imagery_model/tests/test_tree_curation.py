import json
import pytest
import subprocess
from urban_tree_ml.tree_curation import tree_records,expand_tree_records,geographic_regions,storage_payload
from urban_tree_ml.feedback import persist_review_payload,load_persisted_reviews
from urban_tree_ml.quality import _render_grouped_registration_html
from urban_tree_ml.tree_curation import migrate_crown_storage


def fixture():
    samples=[dict(sample_id=s,tree_id='tree',scene_id=s,longitude=-71.1,latitude=42.3,
                  target_x=x,target_y=20,transform_a=.6,transform_b=0,transform_d=0,transform_e=-.6,
                  split='train') for s,x in [('small',10),('big',100)]]
    return {'metadata':{'review_id':'image-v1','source_raster':'image.tif','curation_crs':'EPSG:26919','curation_schema_version':2},
            'samples':samples,'scenes':[{'scene_id':s,'sample_ids':[s]} for s in ['small','big']]}


@pytest.mark.parametrize('evidence', [None, '', 'field survey'])
def test_added_species_evidence_optional_through_storage(tmp_path, evidence):
    manifest = fixture()
    (tmp_path/'manifest.json').write_text(json.dumps(manifest))
    region = dict(region_id='added', mode='confirmed-tree', radius_m=3,
                  longitude=-71.1, latitude=42.3, source='human', species='Acer rubrum')
    if evidence is not None:
        region['identification_source'] = evidence
    persist_review_payload(tmp_path, {'reviews': {}, 'mask_regions': [region]})
    saved = load_persisted_reviews(tmp_path)['mask_regions'][0]
    assert saved['species'] == 'Acer rubrum'
    assert saved.get('identification_source', '') == (evidence or '')
    html = _render_grouped_registration_html([], {}, [])
    assert 'Identification source / evidence (optional)' in html
    assert 'Add the source of this identification' not in html


def test_crown_migration_preserves_tree_center_masks_and_added_identity(tmp_path):
    manifest = fixture()
    (tmp_path/'manifest.json').write_text(json.dumps(manifest))
    tree = dict(status='offset', corrected_longitude=-71.1001, corrected_latitude=42.3001)
    base = dict(longitude=-71.1, latitude=42.3, source='human')
    legacy = dict(schema_version=2, tree_reviews={'tree':tree}, scene_reviews={'small':{'done':True}},
                  mask_regions=[dict(base, region_id='linked', mode='confirmed-tree', tree_id='tree', radius_m=8),
                                dict(base, region_id='added', mode='confirmed-tree', radius_m=3, species='Acer rubrum', identification_source='field survey'),
                                dict(base, region_id='mask', mode='protect', radius_m=12)])
    path=tmp_path/'reviews.json';path.write_text(json.dumps(legacy))
    before=load_persisted_reviews(tmp_path)
    migrated=migrate_crown_storage(legacy)
    assert migrate_crown_storage(migrated)==migrated
    assert migrated['tree_reviews']['tree']==dict(tree,crown_radius_m=8,crown_source='human')
    assert [r['region_id'] for r in migrated['mask_regions']]==['mask']
    assert migrated['added_trees']['manual-added']['species']=='Acer rubrum'
    path.write_text(json.dumps(migrated))
    assert load_persisted_reviews(tmp_path)==before
    # A radius update and then deletion survive the full save/expand path.
    for review in before['reviews'].values(): review['crown_radius_m']=9
    persist_review_payload(tmp_path,before)
    current=load_persisted_reviews(tmp_path)
    assert current['reviews']['big']['crown_radius_m']==9
    for review in current['reviews'].values():
        review.pop('crown_radius_m');review.pop('crown_source')
    persist_review_payload(tmp_path,current)
    assert 'crown_radius_m' not in load_persisted_reviews(tmp_path)['reviews']['small']


def test_crown_migration_refuses_conflicting_measurements():
    with pytest.raises(ValueError,match='Conflicting'):
        migrate_crown_storage(dict(tree_reviews={'tree':{'crown_radius_m':7}}, mask_regions=[
            dict(mode='confirmed-tree',tree_id='tree',radius_m=8)]))


def test_tree_offsets_roundtrip_across_crops_and_durable_storage(tmp_path):
    manifest=fixture();(tmp_path/'manifest.json').write_text(json.dumps(manifest))
    value={'status':'offset','east_m':6.,'north_m':-3.,'image_x':20.,'image_y':25.}
    result=persist_review_payload(tmp_path,{'reviews':{'small':value}})
    raw=json.loads((tmp_path/'reviews.json').read_text())
    assert 'reviews' not in raw and list(raw['tree_reviews'])==['tree']
    assert 'image_x' not in raw['tree_reviews']['tree']
    state=load_persisted_reviews(tmp_path)
    assert state['state_revision']==result['state_revision']
    assert state['reviews']['small']==state['reviews']['big']
    assert state['reviews']['big']['east_m']==pytest.approx(6,abs=.0001)
    persist_review_payload(tmp_path,{**state,'base_revision':state['state_revision']})


def test_geographic_mask_is_independent_of_origin_scene():
    m=fixture();region={'region_id':'r','scene_id':'small','anchor_sample_id':'small',
                        'mode':'protect','east_m':6,'north_m':-3,'radius_m':10}
    masks=geographic_regions([region],m)
    assert not {'scene_id','anchor_sample_id','image_x'} & masks[0].keys()
    assert masks[0]['longitude']>-71.1 and masks[0]['latitude']<42.3
    assert geographic_regions(masks,m)==masks


def test_migration_rejects_conflicting_tree_edits():
    with pytest.raises(ValueError,match='Conflicting'):
        tree_records({'small':{'status':'aligned'},'big':{'status':'not-tree'}},fixture())


def test_ui_shares_tree_correction_and_projects_mask_into_both_crops():
    html=_render_grouped_registration_html([],{},[])
    a=html.index('const withAlignedDefaults =');b=html.index('const median =',a)
    c=html.index('function regionForScene(');d=html.index('function renderMaskRegions',c)
    js='''
const metadata={curation_schema_version:2};
const samples=['small','big'].map((id,i)=>({sample_id:id,tree_id:'t',target_x:10+90*i,target_y:20,
 world_x:1000,world_y:2000,transform_a:.6,transform_b:0,transform_d:0,transform_e:-.6}));
const samplesById=Object.fromEntries(samples.map(s=>[s.sample_id,s]));
function regionPixelScaleM(){return .6}
''' + html[a:b] + html[c:d] + '''
const reviews=withAlignedDefaults({});
reviews.small={status:'offset',east_m:6,north_m:-3,image_x:20,image_y:25};
if(reviews.big.east_m!==6||reviews.big.image_x!=null)throw Error('crop-local coordinates leaked');
reviews.big={status:'not-tree'};
if(reviews.small.status!=='not-tree')throw Error('tree status not shared');
const mask={world_x:1006,world_y:1997,radius_m:5};
const small=regionForScene(mask,{scene_id:'small',sample_ids:['small'],image_width:128,image_height:128});
const big=regionForScene(mask,{scene_id:'big',sample_ids:['big'],image_width:256,image_height:256});
if(small.image_x!==20||big.image_x!==110||big.image_y!==25)throw Error('mask projection');
if(regionForScene({...mask,world_x:99999},{sample_ids:['small'],image_width:128,image_height:128}))throw Error('far mask visible');
'''
    subprocess.run(['node','-e',js],check=True,capture_output=True,text=True)
