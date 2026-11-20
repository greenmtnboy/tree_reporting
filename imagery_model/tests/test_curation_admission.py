import json

import numpy as np
import pandas as pd
import pytest
import rasterio
from pyproj import Transformer

from test_fairfax_curation import fixture, write
from urban_tree_ml.boundary_shifts import training_footprint
from urban_tree_ml.chips import build_chips
from urban_tree_ml.curation_admission import enable, load, touched_tiles
from urban_tree_ml.fairfax_curation import freeze_training
from urban_tree_ml.targets import build_targets, DetectionMaskRegion


def prepared(tmp_path):
    config, taxonomy, review, manifest, state, _ = fixture(tmp_path)
    lon, lat = Transformer.from_crs('EPSG:32618','EPSG:4326',always_xy=True).transform(300076.8,4319923.2)
    feedback = json.loads((review/'training-feedback.json').read_text())
    feedback['region_overrides'] = [dict(region_id='empty-water', mode='confirmed-background',
        splits=['train','validation'], anchor_longitude=lon, anchor_latitude=lat,
        east_m=0.,north_m=0.,radius_m=10.,source='human')]
    write(review/'training-feedback.json', feedback)
    frozen = tmp_path/'frozen'
    freeze_training(tmp_path,frozen,taxonomy)
    enable(frozen/'usffx')
    config.inventory.city = 'USBOS'
    config.split.guard_m = 0
    with rasterio.open(config.imagery.local_raster) as source:
        for seed in range(1000):
            config.seed = seed
            if training_footprint(source,0,0,512,config):
                break
        else:
            raise AssertionError('No training fixture seed')
    return config, frozen/'usffx'


def test_empty_region_materializes_without_inventory_and_only_supervises_polygon(tmp_path):
    config, frozen = prepared(tmp_path)
    result = build_chips(config,config.imagery.local_raster,feedback_path=frozen/'training-feedback.json',
        inventory_source=frozen/'inventory.parquet',output_dataset='negative')
    frame = pd.read_parquet(result['manifest'])
    assert frame.chip_id.tolist() == ['r000000_c000000']
    assert frame.split.tolist() == ['train'] and result['trees'] == 0
    with np.load(tmp_path/'chips/negative'/frame.path.iloc[0]) as chip:
        mask = chip['detection_mask']
        assert 0 < mask.sum() < mask.size/10
        assert mask[mask.shape[0]//2,mask.shape[1]//2] == 1
        assert mask[0,0] == 0 and chip['center'].sum() == 0
    receipt = json.loads((tmp_path/'chips/negative/curation-admission.json').read_text())
    assert receipt['missing_training_candidates'] == []
    assert receipt['materialized'][0]['strict_curated_mask']


def test_legacy_build_does_not_silently_change_its_cohort(tmp_path):
    config, frozen = prepared(tmp_path)
    (frozen/'tile-admission.json').unlink()
    with pytest.raises(ValueError,match='no training pixels'):
        build_chips(config,config.imagery.local_raster,feedback_path=frozen/'training-feedback.json',
            inventory_source=frozen/'inventory.parquet',output_dataset='legacy')


def test_heldout_region_cannot_create_training_window(tmp_path):
    config, frozen = prepared(tmp_path)
    with rasterio.open(config.imagery.local_raster) as source:
        for seed in range(1000):
            config.seed = seed
            if not training_footprint(source,0,0,512,config):
                break
    with pytest.raises(ValueError,match='no training pixels'):
        build_chips(config,config.imagery.local_raster,feedback_path=frozen/'training-feedback.json',
            inventory_source=frozen/'inventory.parquet',output_dataset='heldout')


def test_publication_change_invalidates_admission(tmp_path):
    _, frozen = prepared(tmp_path)
    (frozen/'published-state.json').write_text('{}')
    with pytest.raises(ValueError,match='checksum mismatch'):
        load(frozen)


def test_partial_mask_never_labels_unreviewed_pixels_background():
    targets = build_targets(64,64,[],stride=4,gaussian_sigma_px=1.,supervision_radius_px=4.,background_mode='curated_only',
        mask_regions=[DetectionMaskRegion(x=32,y=32,radius=8,mode='confirmed-background')])
    assert targets['detection_mask'][0,0] == 0
    assert targets['detection_mask'][8,8] == 1
    assert targets['center'].sum() == 0


def test_fairfax_partial_training_with_full_validation(tmp_path):
    from test_fairfax_curation import publish, RUN
    config,taxonomy,review,manifest,state,_ = fixture(tmp_path)
    state['scene_reviews'].pop('s0')
    publish(config,review,manifest,state)
    lon,lat = Transformer.from_crs('EPSG:32618','EPSG:4326',always_xy=True).transform(300076.8,4319923.2)
    feedback=json.loads((review/'training-feedback.json').read_text())
    feedback['region_overrides']=[dict(region_id='water',mode='confirmed-background',splits=['train'],
        anchor_longitude=lon,anchor_latitude=lat,east_m=0.,north_m=0.,radius_m=10.)]
    write(review/'training-feedback.json',feedback)
    frozen=tmp_path/'frozen'
    receipt=freeze_training(tmp_path,frozen,taxonomy,any_curation=True)
    assert receipt['admitted']=={'train':['r000000_c000000'],'validation':['r000000_c000001']}
    result=build_chips(config,config.imagery.local_raster,feedback_path=frozen/'usffx/training-feedback.json',
        inventory_source=frozen/'usffx/inventory.parquet',output_dataset='partial-fairfax')
    frame=pd.read_parquet(result['manifest'])
    row=frame[frame.split.eq('train')].iloc[0]
    with np.load(tmp_path/'chips/partial-fairfax'/row.path) as chip:
        assert chip['detection_mask'][0,0]==0
        assert chip['detection_mask'].sum()>0
    from urban_tree_ml.curated_validation import freeze_policy, restrict_sources
    from urban_tree_ml.fairfax_curation import verify_dataset
    freeze_policy(frozen,['usffx'])
    (tmp_path/'run').mkdir()
    restrict_sources(frozen,{'usffx':tmp_path/'chips/partial-fairfax'},tmp_path/'run')
    verify_dataset(tmp_path/'chips/partial-fairfax')


def test_confirmed_tree_crown_does_not_admit_adjacent_centerless_tile(tmp_path):
    config,_,_,manifest,state,_=fixture(tmp_path)
    lon,lat=Transformer.from_crs('EPSG:32618','EPSG:4326',always_xy=True).transform(300153.3,4319923.2)
    state['scene_reviews']={}
    region=dict(region_id='edge-tree',mode='confirmed-tree',splits=['train'],
        anchor_longitude=lon,anchor_latitude=lat,east_m=0.,north_m=0.,radius_m=10.)
    with rasterio.open(config.imagery.local_raster) as source:
        ids=touched_tiles(manifest,state,source,512,{'region_overrides':[region]})
    assert set(ids)=={'r000000_c000000'}


def test_full_review_uses_geographic_footprint_across_review_resolutions(tmp_path):
    config,_,_,_,_,_=fixture(tmp_path)
    lon,lat=Transformer.from_crs('EPSG:32618','EPSG:4326',always_xy=True).transform(300076.8,4319923.2)
    anchor=dict(longitude=lon,latitude=lat,target_x=128,target_y=128,
        transform_a=.6,transform_b=0.,transform_d=0.,transform_e=-.6)
    scene=dict(scene_id='full',image_width=256,image_height=256,anchor=anchor,splits=['train'])
    state=dict(reviews={},scene_reviews={'full':dict(done=True,more_done=True)})
    with rasterio.open(config.imagery.local_raster) as source:
        touched=touched_tiles(dict(samples=[],scenes=[scene]),state,source,512,{})
        assert 'full-more-done-tile' in touched['r000000_c000000']
        scene.update(image_width=128,image_height=128)
        touched=touched_tiles(dict(samples=[],scenes=[scene]),state,source,512,{})
        assert all('full-more-done-tile' not in reasons for reasons in touched.values())


def test_empty_background_batch_has_finite_suppressive_gradient():
    import torch
    from urban_tree_ml.losses import masked_centernet_focal_loss
    logits=torch.full((2,1,32,32),-1.9009588,requires_grad=True)
    loss=masked_centernet_focal_loss(logits,torch.zeros_like(logits),torch.ones_like(logits))
    loss.backward()
    assert torch.isfinite(loss) and torch.isfinite(logits.grad).all()
    assert (logits.grad>0).all()  # Gradient descent suppresses every false centre.
