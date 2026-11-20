import json

import numpy as np
import pandas as pd
import pytest
import rasterio
from rasterio.transform import from_origin

from test_curation_admission import prepared
from test_fairfax_curation import write
from urban_tree_ml.chips import build_chips
from urban_tree_ml.curation_admission import frozen_context
from urban_tree_ml.prepared_finetune import sha
from urban_tree_ml.temporal_training import admitted_splits


def temporal_fixture(tmp_path, city='usbos', year=2025):
    config, directory = prepared(tmp_path)
    config.inventory.city = city.upper()
    config.imagery.acquisition_id = f'{city}-naip-{year}'
    config.imagery.boundary_shifts = False
    original = directory/'imagery.vrt'
    with rasterio.open(original, 'w', driver='GTiff', width=768, height=256, count=4,
                       dtype='uint8', crs='EPSG:32618', transform=from_origin(300000,4320000,.6,.6)) as target:
        target.write(np.full((4,256,768),100,dtype=np.uint8))
    feedback = json.loads((directory/'training-feedback.json').read_text())
    feedback['source_raster_name'] = original.name
    write(directory/'training-feedback.json', feedback)
    integrity = json.loads((directory/'integrity.json').read_text())
    integrity['sha256']['training-feedback.json'] = sha(directory/'training-feedback.json')
    write(directory/'integrity.json', integrity)
    admission = json.loads((directory/'tile-admission.json').read_text())
    admission['integrity_sha256'] = sha(directory/'integrity.json')
    write(directory/'tile-admission.json', admission)
    reference = pd.DataFrame({'chip_id':['r000000_c000000'], 'split':['train']})
    reference.to_parquet(directory/'reference-chips.parquet', index=False)
    manifest, state, feedback = frozen_context(directory)
    with rasterio.open(config.imagery.local_raster) as source:
        planned = admitted_splits(manifest,state,feedback,source,512,reference)
    paths = [original, config.imagery.local_raster, directory/'reference-chips.parquet']
    policy = dict(schema_version=1, test_evaluated=False, acquisition_id=config.imagery.acquisition_id,
                  city=city, seed=config.seed, split=config.split.model_dump(mode='json'),
                  model_raster=config.imagery.local_raster.relative_to(tmp_path).as_posix(),
                  source_raster=original.relative_to(tmp_path).as_posix(), candidates=planned,
                  admitted=planned, excluded_nodata=[],
                  checksums={p.relative_to(tmp_path).as_posix():sha(p) for p in paths})
    write(directory/'temporal-admission.json', policy)
    return config, directory


@pytest.mark.parametrize('city,year', [('usbos',2025), ('usffx',2023)])
def test_temporal_background_tile_uses_only_published_polygon(tmp_path, city, year):
    config, directory = temporal_fixture(tmp_path, city, year)
    result = build_chips(config, config.imagery.local_raster,
        feedback_path=directory/'training-feedback.json', inventory_source=directory/'inventory.parquet',
        output_dataset='temporal-curated')
    frame = pd.read_parquet(result['manifest'])
    assert frame.chip_id.tolist() == ['r000000_c000000']
    assert frame.split.tolist() == ['train']
    with np.load(tmp_path/'chips/temporal-curated'/frame.path.iloc[0]) as payload:
        assert payload['center'].sum() == 0
        assert 0 < payload['detection_mask'].sum() < payload['detection_mask'].size/10
        assert payload['detection_mask'][0,0] == 0


@pytest.mark.parametrize('change', ['year', 'split', 'pixels', 'publication'])
def test_temporal_build_rejects_wrong_identity_or_changed_inputs(tmp_path, change):
    config, directory = temporal_fixture(tmp_path)
    if change == 'year':
        config.imagery.acquisition_id = 'usbos-naip-2021'
    elif change == 'split':
        config.seed += 1
    elif change == 'pixels':
        with rasterio.open(config.imagery.local_raster,'r+') as image:
            image.write(np.zeros((4,512,1536),dtype=np.uint8))
    else:
        (directory/'reviews.json').write_text('{}')
    with pytest.raises(ValueError):
        build_chips(config, config.imagery.local_raster, feedback_path=directory/'training-feedback.json',
                    inventory_source=directory/'inventory.parquet', output_dataset='rejected')
    assert not (tmp_path/'chips/rejected').exists()


def test_temporal_partial_validation_and_uncurated_train_stay_out(tmp_path):
    config, directory = temporal_fixture(tmp_path)
    manifest, state, feedback = frozen_context(directory)
    reference = pd.DataFrame({'chip_id':['r000000_c000000','r000000_c000001'],
                              'split':['validation','train']})
    state['scene_reviews'] = {}
    with rasterio.open(config.imagery.local_raster) as source:
        assert admitted_splits(manifest,state,feedback,source,512,reference) == {}
    reference.loc[0,'split'] = 'test'
    with rasterio.open(config.imagery.local_raster) as source, pytest.raises(ValueError,match='development'):
        admitted_splits(manifest,state,feedback,source,512,reference)


def test_temporal_validation_requires_full_footprint_more_done(tmp_path):
    from pyproj import Transformer
    config, directory = temporal_fixture(tmp_path)
    lon, lat = Transformer.from_crs('EPSG:32618','EPSG:4326',always_xy=True).transform(300076.8,4319923.2)
    manifest = {'samples': [], 'scenes': [{'scene_id': 'review', 'splits': ['validation'],
        'image_width': 256, 'image_height': 256, 'anchor': {
            'longitude': lon, 'latitude': lat, 'target_x': 128, 'target_y': 128,
            'transform_a': .6, 'transform_b': 0., 'transform_d': 0., 'transform_e': -.6}}]}
    state = {'reviews': {}, 'scene_reviews': {'review': {'done': True}}}
    reference = pd.DataFrame({'chip_id': ['r000000_c000000'], 'split': ['validation']})
    with rasterio.open(config.imagery.local_raster) as source:
        assert admitted_splits(manifest,state,{},source,512,reference) == {}
        state['scene_reviews']['review']['more_done'] = True
        assert admitted_splits(manifest,state,{},source,512,reference) == {'r000000_c000000': 'validation'}
        manifest['scenes'][0]['image_width'] = 128
        assert admitted_splits(manifest,state,{},source,512,reference) == {}
        manifest['scenes'][0]['image_width'] = 256
        manifest['scenes'][0]['anchor']['target_x'] -= .00005
        reference = pd.DataFrame({'chip_id': ['r000000_c000000','r000000_c000001'],
                                  'split': ['train','train']})
        # A 0.0001-model-pixel geographic roundtrip must not admit a neighbor.
        assert admitted_splits(manifest,state,{},source,512,reference) == {'r000000_c000000': 'train'}


def test_portable_nested_warp_preserves_pixels_after_root_relocation(tmp_path):
    import shutil
    from pathlib import Path
    from rasterio.vrt import WarpedVRT
    from rasterio.shutil import copy as raster_copy
    from prepare_temporal_training import portable_raster
    root = tmp_path/'original'
    root.mkdir()
    photo = root/'photo.tif'
    values = np.arange(4*16*16,dtype=np.uint16).reshape(4,16,16)
    with rasterio.open(photo,'w',driver='GTiff',width=16,height=16,count=4,dtype='uint16',
                       crs='EPSG:32618',transform=from_origin(300000,4320000,.6,.6)) as image:
        image.write(values)
    warp = root/'warp.vrt'
    with rasterio.open(photo) as image, WarpedVRT(image) as virtual:
        raster_copy(virtual,warp,driver='VRT')
    destination = root/'run-inputs'/'frozen'
    destination.mkdir(parents=True)
    assets = {}
    portable = portable_raster(warp,destination,root,assets,{})
    assert set(assets) == {'photo.tif'}
    moved = tmp_path/'relocated'
    shutil.copytree(root,moved)
    # A moved copy must read only dependencies under its own new root.
    with rasterio.open(moved/portable.relative_to(root)) as image:
        assert np.array_equal(image.read(),values)
        assert all(Path(path).is_relative_to(moved) for path in image.files)


def test_joint_keeps_years_distinct_and_rejects_changed_split(tmp_path):
    import shutil
    from urban_tree_ml.joint import combine_manifests
    config, original = temporal_fixture(tmp_path)
    sources = {}
    for year in (2021, 2025):
        key = f'usbos-naip-{year}'
        candidate = config.model_copy(deep=True)
        candidate.imagery.acquisition_id = key
        frozen = tmp_path/'run-inputs'/candidate.experiment/key
        shutil.copytree(original, frozen)
        policy = json.loads((frozen/'temporal-admission.json').read_text())
        policy['acquisition_id'] = key
        write(frozen/'temporal-admission.json', policy)
        output = 'curated-'+key
        build_chips(candidate, candidate.imagery.local_raster,
                    feedback_path=frozen/'training-feedback.json',
                    inventory_source=frozen/'inventory.parquet', output_dataset=output)
        candidate.dataset = output
        directory = tmp_path/'chips'/output
        write(directory/'temporal-dataset.json', {'config': candidate.model_dump(mode='json')})
        sources[key] = directory
    result = combine_manifests(sources, tmp_path/'chips/joint')
    assert result['cities'] == {'usbos-naip-2021': {'train': 1}, 'usbos-naip-2025': {'train': 1}}
    joint = pd.read_parquet(tmp_path/'chips/joint/chips.parquet')
    assert joint.chip_id.is_unique and set(joint.city) == set(sources)
    assert set(joint.source_chip_id) == {'r000000_c000000'}
    changed = sources['usbos-naip-2025']/'chips.parquet'
    frame = pd.read_parquet(changed)
    frame['split'] = 'validation'
    frame.to_parquet(changed, index=False)
    with pytest.raises(ValueError, match='coverage differs'):
        combine_manifests(sources, tmp_path/'chips/rejected-joint')
