import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import rasterio
from rasterio.transform import from_origin

from urban_tree_ml.config import load_config, ReferenceConfig
from urban_tree_ml.fairfax_curation import (
    RUN, admitted_ids, freeze_training, verify_dataset, verify_training_admission,
)
from urban_tree_ml.fairfax_pilot import sha256
from urban_tree_ml.feedback import _json_sha256, _review_state_sha256
from urban_tree_ml.chips import build_chips
from urban_tree_ml.joint import combine_manifests


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def fixture(tmp_path):
    config = load_config(Path(__file__).parents[1] / 'configs/sf_naip_smoke.yaml')
    config.paths.root = tmp_path
    config.inventory.city = 'USFFX'
    config.dataset = RUN + '-usffx'
    config.imagery.chip_pixels = 512
    config.imagery.resolution_m = .3
    config.imagery.boundary_shifts = False
    config.split.projected_crs = 'EPSG:32618'
    raster = tmp_path / 'source.tif'
    with rasterio.open(raster, 'w', driver='GTiff', width=1536, height=512, count=4,
                       dtype='uint8', crs='EPSG:32618', transform=from_origin(300000,4320000,.3,.3)) as target:
        target.write(np.full((4,512,1536), 100, dtype=np.uint8))
    config.imagery.local_raster = raster
    config.imagery.planting_date_cutoff = date(2023,10,11)
    taxonomy = tmp_path / 'taxonomy.json'
    write(taxonomy, {'species': ['Acer rubrum'], 'genera': ['Acer']})
    normalization = tmp_path / 'normalization.json'
    write(normalization, {'mean': [.5]*4, 'std': [.2]*4})
    config.reference = ReferenceConfig(taxonomy_path=taxonomy, normalization_path=normalization)
    inventory = tmp_path / 'inventory/usffx/inventory.parquet'
    inventory.parent.mkdir(parents=True)
    fields = {'tree_id': 'str', 'split': 'str', 'plant_date':'str', 'longitude': 'float64', 'latitude': 'float64',
              'dbh_log1p': 'float64', 'genus_id': 'int64', 'species_id': 'int64',
              'split_eligible': 'bool', 'dbh_eligible': 'bool', 'genus_eligible': 'bool',
              'species_eligible': 'bool'}
    pd.DataFrame({name: pd.Series(dtype=dtype) for name,dtype in fields.items()}).to_parquet(inventory)
    inputs = tmp_path / 'run-inputs' / RUN
    write(inputs / 'config.json', config.model_dump(mode='json'))
    plan = {'windows': [dict(row=0,col=col,split=split) for col,split in
                        [(0,'train'), (512,'validation'), (1024,'train')]]}
    write(inputs / 'sample-plan.json', plan)
    write(inputs / 'registration-receipt.json', {
        'sample_plan_sha256': sha256(inputs / 'sample-plan.json'),
        'raster_sha256': sha256(raster), 'source_files': [{'path':str(raster),'sha256':sha256(raster)}]})
    scenes = [dict(scene_id=f's{index}', validation_chip_id=f'r000000_c{index:06d}',
                   image_width=512,image_height=512,splits=[w['split']])
              for index,w in enumerate(plan['windows'])]
    manifest = {'metadata': {'review_id':'fixture'}, 'samples': [], 'scenes': scenes}
    review = tmp_path / 'qa/registration' / RUN
    write(review / 'manifest.json', manifest)
    state = {'reviews': {}, 'scene_reviews': {
        's0': {'done':True,'more_done':True}, 's1': {'done':True,'more_done':True},
        's2': {'done':True}}, 'mask_regions': []}
    publish(config, review, manifest, state)
    return config, taxonomy, review, manifest, state, plan


def publish(config, review, manifest, state):
    write(review / 'reviews.json', state)
    write(review / 'training-feedback.json', {
        'schema_version':1, 'dataset':config.dataset, 'source_raster_name':config.imagery.local_raster.name,
        'source_manifest_sha256':_json_sha256(manifest),
        'source_reviews_sha256':_review_state_sha256(state['reviews'], state['scene_reviews'], state['mask_regions']),
        'registration': {'east_m':0., 'north_m':0.}, 'exclusions':[], 'point_corrections':[],
        'region_overrides':[]})


def test_full_tile_explicit_completion_and_split_guards(tmp_path):
    _,_,_,manifest,state,plan = fixture(tmp_path)
    assert admitted_ids(manifest,state,plan) == {'train':['r000000_c000000'],'validation':['r000000_c000001']}
    manifest['scenes'][0]['image_width'] = 256
    with pytest.raises(ValueError, match='full planned'):
        admitted_ids(manifest,state,plan)
    plan['windows'][0]['split'] = 'test'
    with pytest.raises(ValueError, match='development split'):
        admitted_ids(manifest,state,plan)


def test_freeze_rejects_missing_or_unpublished_completion(tmp_path):
    config,taxonomy,review,manifest,state,_ = fixture(tmp_path)
    state['scene_reviews']['s1'] = {'done':True}
    publish(config,review,manifest,state)
    with pytest.raises(ValueError,match='both train and validation'):
        freeze_training(tmp_path,tmp_path/'frozen',taxonomy)
    assert not (tmp_path/'frozen').exists()
    state['scene_reviews']['s1']['more_done'] = True
    write(review/'reviews.json',state)
    with pytest.raises(ValueError,match='publish current'):
        freeze_training(tmp_path,tmp_path/'frozen',taxonomy)


def test_real_empty_curated_tiles_build_and_joint_training_guard(tmp_path):
    config,taxonomy,_,_,_,_ = fixture(tmp_path)
    frozen = tmp_path/'frozen'
    freeze_training(tmp_path,frozen,taxonomy)
    args = dict(feedback_path=frozen/'usffx/training-feedback.json',
                inventory_source=frozen/'usffx/inventory.parquet',output_dataset='curated')
    result = build_chips(config,config.imagery.local_raster,**args)
    directory = tmp_path/'chips/curated'
    frame = verify_dataset(directory)
    assert len(frame)==2 and result['trees']==0
    assert set(frame.chip_id)=={'r000000_c000000','r000000_c000001'}
    for path in frame.path:
        with np.load(directory/path) as chip:
            assert np.count_nonzero(chip['detection_mask'])>0
    config.dataset='curated'
    verify_training_admission(config,directory/'chips.parquet')
    joint = tmp_path/'chips/joint'
    combine_manifests({'usffx':directory},joint)
    config.inventory.city='US SFO'
    verify_training_admission(config,joint/'chips.parquet')
    rows=pd.read_parquet(joint/'chips.parquet')
    rows.loc[0,'source_chip_id']='uncurated'
    rows.to_parquet(joint/'chips.parquet')
    with pytest.raises(ValueError,match='Joint Fairfax'):
        verify_training_admission(config,joint/'chips.parquet')
    (directory/frame.path.iloc[0]).write_bytes(b'changed')
    with pytest.raises(ValueError,match='dataset changed'):
        verify_dataset(directory)


def test_direct_build_rejects_unfrozen_fairfax_and_joint_rejects_unknown_labels(tmp_path):
    config,_,_,_,_,_=fixture(tmp_path)
    with pytest.raises(ValueError,match='explicit frozen'):
        build_chips(config,config.imagery.local_raster)
    unknown=tmp_path/'unknown'
    write(unknown/'summary.json',{'label_status':'unavailable'})
    with pytest.raises(ValueError,match='Prediction-only'):
        combine_manifests({'usffx':unknown},tmp_path/'joint')


def test_human_confirmed_tree_survives_real_target_build(tmp_path):
    from pyproj import Transformer
    from urban_tree_ml.confirmed_trees import confirmed_tree_rows

    config,taxonomy,review,_,_,_=fixture(tmp_path)
    config.split.guard_m=0
    longitude,latitude=Transformer.from_crs('EPSG:32618','EPSG:4326',always_xy=True).transform(300076.8,4319923.2)
    region=dict(region_id='human',mode='confirmed-tree',splits=['train'],
                anchor_longitude=longitude,anchor_latitude=latitude,east_m=0.,north_m=0.,radius_m=2.)
    for seed in range(100):
        config.seed=seed
        if not confirmed_tree_rows([region],config,'EPSG:32618').empty:
            break
    else:
        raise AssertionError('No train seed in fixture')
    write(tmp_path/'run-inputs'/RUN/'config.json',config.model_dump(mode='json'))
    feedback=json.loads((review/'training-feedback.json').read_text())
    feedback['region_overrides']=[region]
    write(review/'training-feedback.json',feedback)
    frozen=tmp_path/'frozen'
    freeze_training(tmp_path,frozen,taxonomy)
    result=build_chips(config,config.imagery.local_raster,
        feedback_path=frozen/'usffx/training-feedback.json',
        inventory_source=frozen/'usffx/inventory.parquet',output_dataset='human-curated')
    assert result['trees']==1
    labels=pd.read_parquet(result['labels'])
    assert labels.tree_id.tolist()==['manual-human']
    assert labels.split.tolist()==['train']
    verify_dataset(tmp_path/'chips/human-curated')
