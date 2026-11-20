import copy
import pytest
from urban_tree_ml.peer_curation import preview_peer_trees


def entry(identity,year,split='train'):
    sample=dict(sample_id='a',tree_id='tree',scene_id='tile',longitude=0.,latitude=0.,
        target_x=50.,target_y=50.,transform_a=1.,transform_b=0.,transform_d=0.,transform_e=-1.,split=split)
    return dict(id=identity,date=f'{year}-07-01',city='usbos',split_policy_sha256='same-grid',
        manifest={'metadata':{'curation_crs':'EPSG:3857','curation_schema_version':2,'review_id':identity},'samples':[sample],
          'scenes':[{'scene_id':'tile','sample_ids':['a'],'image_width':100,'image_height':100,'splits':[split]}]},
        state={'reviews':{},'mask_regions':[],'scene_reviews':{}})


def test_closest_curated_year_preserves_edits_and_does_not_copy_completion():
    target=entry('new',2024);older=entry('old',2022);near=entry('near',2023)
    older['state']['reviews']['a']={'status':'aligned','source':'human'}
    near['state']['reviews']['a']={'status':'offset','east_m':2.,'north_m':3.,'source':'human','crown_radius_m':4.}
    near['state']['scene_reviews']['tile']={'more_done':True}
    before=copy.deepcopy(target)
    result=preview_peer_trees(target,'tile',[older,near])
    assert result['source']['id']=='near' and result['reviews']['a']['east_m']==pytest.approx(2.)
    assert result['reviews']['a']['north_m']==pytest.approx(3.)
    assert 'scene_reviews' not in result and target==before
    target['state']['reviews']['a']={'status':'aligned','source':'inventory-suggestion'}
    assert preview_peer_trees(target,'tile',[near])['copied']==1
    target['state']['reviews']['a']={'status':'not-tree','source':'human'}
    assert preview_peer_trees(target,'tile',[near])['copied']==0


def test_development_split_difference_imports_but_holdouts_and_policy_drift_do_not():
    target=entry('new',2024);peer=entry('old',2023)
    peer['state']['reviews']['a']={'status':'aligned','source':'model'}
    peer['state']['mask_regions']=[dict(mode='confirmed-background',region_id='b',world_x=0,world_y=0,radius_m=8,source='human')]
    result=preview_peer_trees(target,'tile',[peer])
    assert result['reviews']=={} and result['copied']==1
    assert result['mask_regions'][0]['mode']=='confirmed-background'
    peer['state']['reviews']['a']={'status':'aligned','source':'human'}
    peer['manifest']['samples'][0]['split']='validation'
    before=copy.deepcopy(target)
    result=preview_peer_trees(target,'tile',[peer])
    assert result['reviews']['a']['peer_source_split']=='validation'
    assert result['reviews']['a']['peer_target_split']=='train'
    assert target==before
    peer['manifest']['samples'][0]['split']='test'
    assert preview_peer_trees(target,'tile',[peer])['reviews']=={}
    with pytest.raises(ValueError,match='holdouts'): preview_peer_trees(entry('test',2024,'test'),'tile',[peer])
    peer['split_policy_sha256']='different'
    assert preview_peer_trees(target,'tile',[peer])['source'] is None


@pytest.mark.parametrize('destination,source',[('train','validation'),('validation','train')])
@pytest.mark.parametrize('matched',[True,False])
def test_cross_split_import_provenance_survives_save_and_scoped_revert(destination,source,matched):
    from urban_tree_ml.peer_curation import preview_peer_revert
    from urban_tree_ml.feedback import normalize_review_payload,normalize_mask_region_payload
    from urban_tree_ml.tree_curation import storage_payload
    target=entry('new',2025,destination);peer=entry('old',2023,source)
    peer['state']['reviews']['a']={'status':'offset','east_m':2.,'north_m':3.,'source':'human','crown_radius_m':4.}
    if not matched:peer['manifest']['samples'][0]['tree_id']='other'
    result=preview_peer_trees(target,'tile',[peer])
    assert result['copied']==1
    normalized=dict(reviews=normalize_review_payload(result,target['manifest']),
                    mask_regions=normalize_mask_region_payload(result,target['manifest']))
    stored=storage_payload(normalized,target['manifest'])
    records=stored['tree_reviews'] if matched else stored['added_trees']
    record=next(iter(records.values()))
    assert record['peer_source_split']==source and record['peer_target_split']==destination
    target['state'].update(reviews=normalize_review_payload(stored,target['manifest']),
                           mask_regions=normalize_mask_region_payload(stored,target['manifest']))
    assert preview_peer_trees(target,'tile',[peer])['copied']==0
    assert preview_peer_revert(target,'tile',[peer])['reverted']==1
    assert target['manifest']['samples'][0]['split']==destination


def test_manual_tree_dedup_and_deterministic_id():
    target=entry('new',2024);peer=entry('old',2023)
    peer['state']['mask_regions']=[dict(mode='confirmed-tree',region_id='manual',world_x=10,world_y=10,radius_m=3,source='human')]
    first=preview_peer_trees(target,'tile',[peer]); assert first['copied']==1
    assert first==preview_peer_trees(target,'tile',[peer])
    target['state']['mask_regions']=first['mask_regions']
    assert preview_peer_trees(target,'tile',[peer])['copied']==0


@pytest.mark.parametrize('status', ['not-tree', 'duplicate', 'uncertain', 'occluded'])
def test_import_preserves_inventory_decisions_with_source_tag(status):
    target=entry('new',2025);peer=entry('old',2023)
    peer['state']['reviews']['a']={'status':status,'source':'human'}
    result=preview_peer_trees(target,'tile',[peer])
    assert result['reviews']['a']['status']==status
    assert result['reviews']['a']['source']=='peer-image:old'
    assert result['mask_regions']==[] and result['completion_flags_copied'] is False


def test_overlap_flags_transfer_without_becoming_human_labels():
    from urban_tree_ml.curation_admission import human_review
    from urban_tree_ml.feedback import normalize_review_payload
    target=entry('new',2025);peer=entry('old',2023)
    peer['state']['reviews']['a']={'status':'occluded','source':'crown-overlap','heuristic_id':'rule1'}
    result=preview_peer_trees(target,'tile',[peer])
    review=normalize_review_payload(result,target['manifest'])['a']
    assert review['status']=='occluded' and review['peer_image']=='old'
    assert review['source']=='crown-overlap' and not human_review(review)
    target['state']['reviews']['a']={'status':'aligned','source':'inventory-suggestion','crown_radius_m':5.}
    assert preview_peer_trees(target,'tile',[peer])['copied']==0


def test_revert_is_scoped_and_preserves_later_edits():
    from urban_tree_ml.peer_curation import preview_peer_revert
    from urban_tree_ml.feedback import normalize_review_payload,normalize_mask_region_payload
    target=entry('new',2025);peer=entry('old',2023)
    peer['state']['reviews']['a']={'status':'not-tree','source':'human'}
    peer['state']['mask_regions']=[dict(mode='confirmed-tree',region_id='manual',world_x=10,world_y=10,radius_m=3,source='human')]
    result=preview_peer_trees(target,'tile',[peer])
    target['state']['reviews']=normalize_review_payload(result,target['manifest'])
    target['state']['mask_regions']=normalize_mask_region_payload(result,target['manifest'])
    before=copy.deepcopy(target)
    undo=preview_peer_revert(target,'tile',[peer])
    assert undo['reverted']==2 and target==before
    assert undo['reviews']['a']=={'status':'aligned','source':'inventory-suggestion'}
    target['state']['reviews']['a']['status']='aligned'
    target['state']['mask_regions'][0]['radius_m']=4.
    assert preview_peer_revert(target,'tile',[peer])['reverted']==0


def test_revert_tolerates_projection_roundoff_but_not_real_movement_or_notes():
    from urban_tree_ml.peer_curation import _same_import
    before={'east_m':1.,'north_m':2.,'source':'peer-image:old'}
    assert _same_import({**before,'east_m':1.+1e-10},before)
    assert not _same_import({**before,'east_m':1.01},before)
    assert not _same_import({**before,'note':'keep this'},before)


@pytest.mark.parametrize('mode',['confirmed-background','protect','confirmed-woods','single-tree'])
def test_area_only_peer_clips_to_tile_reimport_and_revert(mode):
    from urban_tree_ml.peer_curation import preview_peer_revert
    from urban_tree_ml.feedback import normalize_mask_region_payload
    from pyproj import Transformer
    target=entry('new',2025);peer=entry('old',2023)
    peer['state']['mask_regions']=[dict(mode=mode,region_id='area',world_x=40.,world_y=0.,radius_m=45.,
         world_polygon=[[20.,-20.],[70.,-20.],[70.,20.],[20.,20.]],source='human')]
    result=preview_peer_trees(target,'tile',[peer])
    assert result['source']['id']=='old' and len(result['mask_regions'])==1
    area=result['mask_regions'][0]
    assert area['mode']==mode and area['source']=='peer-image:old'
    projected=Transformer.from_crs('EPSG:4326','EPSG:3857',always_xy=True)
    xs,ys=projected.transform(*zip(*area['polygon']))
    assert max(xs)==pytest.approx(50.) and min(xs)==pytest.approx(20.)
    assert min(ys)>=-50 and max(ys)<=50
    target['state']['mask_regions']=normalize_mask_region_payload(result,target['manifest'])
    assert preview_peer_trees(target,'tile',[peer])['copied']==0
    assert preview_peer_revert(target,'tile',[peer])['remove_mask_regions']==[area['region_id']]
    target['state']['mask_regions'][0]['source']='human'
    assert preview_peer_revert(target,'tile',[peer])['reverted']==0


def test_crossed_freehand_area_retains_both_filled_lobes():
    from shapely.geometry import Polygon,Point
    from shapely.ops import unary_union
    from pyproj import Transformer
    target=entry('new',2025);peer=entry('old',2023)
    peer['state']['mask_regions']=[dict(mode='confirmed-background',region_id='crossed',world_x=0.,world_y=0.,
            radius_m=30.,world_polygon=[[-20.,-20.],[20.,20.],[-20.,20.],[10.,-20.]],source='human')]
    result=preview_peer_trees(target,'tile',[peer])
    assert len(result['mask_regions'])==2
    project=Transformer.from_crs('EPSG:4326','EPSG:3857',always_xy=True)
    shape=unary_union([Polygon(zip(*project.transform(*zip(*r['polygon'])))) for r in result['mask_regions']])
    assert shape.area>600.
    assert shape.contains(Point(0,10)) and shape.contains(Point(0,-10))
    assert not shape.contains(Point(15,0))
