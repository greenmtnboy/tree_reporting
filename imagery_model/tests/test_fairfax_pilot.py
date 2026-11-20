import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
import rasterio
from pyproj import Transformer
from rasterio.transform import from_origin
from shapely.geometry import box, mapping
from shapely.ops import transform

from urban_tree_ml.config import ReferenceConfig, load_config
from urban_tree_ml.fairfax_pilot import audit_bundle, load_bundle, sample_windows, select_items, sha256
from urban_tree_ml.prediction_chips import build_prediction_chips
from urban_tree_ml.splits import Block, split_for_block


def config(tmp_path):
    cfg = load_config(Path(__file__).parents[1] / 'configs/sf_naip_smoke.yaml')
    cfg.paths.root = tmp_path
    cfg.imagery.chip_pixels = 512
    cfg.imagery.resolution_m = .3
    cfg.split.projected_crs = 'EPSG:32618'
    cfg.dataset = 'fairfax-test'
    normalization = tmp_path / 'reference.json'
    normalization.write_text(json.dumps({'mean': [.5] * 4, 'std': [.2] * 4}))
    cfg.reference = ReferenceConfig(taxonomy_path=tmp_path / 'taxonomy.json', normalization_path=normalization)
    return cfg


def bundle(tmp_path):
    boundary = tmp_path / 'boundary.geojson'
    boundary.write_text(json.dumps(mapping(box(-77.5, 38.6, -77.1, 39))))
    manifest = {'schema_version': 1, 'city': 'USFFX', 'jurisdiction': 'Fairfax County, Virginia',
                'boundary': boundary.name, 'inventory': None, 'calibration_inventory': None,
                'sources': [dict(name='fixture', url='https://example.com', retrieved_at='2026-11-06',
                                 jurisdiction='Fairfax County', license='fixture', coverage='boundary')],
                'limitations': ['Inventory unavailable'], 'sha256': {boundary.name: sha256(boundary)}}
    (tmp_path / 'manifest.json').write_text(json.dumps(manifest))
    taxonomy = tmp_path / 'taxonomy.json'
    taxonomy.write_text(json.dumps({'species': ['Acer rubrum'], 'genera': ['Acer']}))
    return manifest, taxonomy


def test_boundary_only_bundle_and_checksum_guard(tmp_path):
    _, taxonomy = bundle(tmp_path)
    report = audit_bundle(tmp_path, taxonomy)
    assert report['inventories']['inventory']['status'] == 'unavailable'
    (tmp_path / 'boundary.geojson').write_text('{}')
    with pytest.raises(ValueError, match='checksum mismatch'):
        load_bundle(tmp_path)


def test_bundle_rejects_path_escape(tmp_path):
    manifest, _ = bundle(tmp_path)
    manifest['boundary'] = '../outside.geojson'
    (tmp_path / 'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='escapes'):
        load_bundle(tmp_path)


def test_acquisition_selection_rejects_year_state_bands_and_nonoverlap():
    item = {'id': 'va_fixture', 'properties': {'datetime': '2023-10-11T00:00:00Z', 'gsd': .6},
            'bbox': [-77.5, 38.6, -77.1, 39.], 'geometry': mapping(box(-77.5, 38.6, -77.1, 39.)),
            'assets': {'image': {'href': 'https://example.com/fixture.tif',
                                  'eo:bands': [{'common_name': name} for name in ['red','green','blue','nir']]}}}
    catalog = {'items': [item]}
    county = box(-77.4, 38.7, -77.2, 38.9)
    assert select_items(catalog, county, 2023, ['va_fixture'])[0]['id'] == 'va_fixture'
    with pytest.raises(ValueError, match='same-year'):
        select_items(catalog, county, 2021, ['va_fixture'])
    with pytest.raises(ValueError, match='intersect'):
        select_items(catalog, box(-80, 38, -79, 39), 2023, ['va_fixture'])
    with pytest.raises(ValueError, match='unique'):
        select_items(catalog, county, 2023, ['va_fixture', 'va_fixture'])
    item['assets']['image']['eo:bands'][-1]['common_name'] = 'alpha'
    with pytest.raises(ValueError, match='RGBN'):
        select_items(catalog, county, 2023, ['va_fixture'])
    item['id'] = 'md_fixture'
    with pytest.raises(ValueError, match='Virginia'):
        select_items(catalog, county, 2023, ['md_fixture'])


def test_acquisition_resume_preserves_request_and_verifies_download(tmp_path, monkeypatch):
    from urban_tree_ml import fairfax_pilot, imagery
    bundle_dir = tmp_path / 'bundle'
    bundle_dir.mkdir()
    bundle(bundle_dir)
    cfg = config(tmp_path)
    artifacts = tmp_path / 'artifacts'
    run = artifacts / 'runs' / fairfax_pilot.PARENT
    run.mkdir(parents=True)
    (run / 'config-usbos.json').write_text(cfg.model_dump_json())
    catalog = tmp_path / 'catalog.json'
    catalog.write_text(json.dumps({'items': [{
        'id': 'va_fixture', 'properties': {'datetime': '2023-10-11T00:00:00Z', 'gsd': .6},
        'bbox': [-77.5, 38.6, -77.1, 39.], 'geometry': mapping(box(-77.5, 38.6, -77.1, 39.)),
        'assets': {'image': {'href': 'https://example.com/fixture.tif',
                             'eo:bands': [{'common_name': name} for name in ['red','green','blue','nir']]}}
    }]}))
    source = tmp_path / 'source.tif'
    source.write_bytes(b'validated raster fixture')
    source_hash = sha256(source)
    attempts = []
    def fetch(config, item_id):
        attempts.append(item_id)
        if len(attempts) == 1:
            raise RuntimeError('interrupted download')
        return {'path': str(source), 'sha256': source_hash}
    monkeypatch.setattr(imagery, 'fetch_stac_item', fetch)
    mosaic = tmp_path / 'mosaic.vrt'
    mosaic.write_text('<fixture/>')
    monkeypatch.setattr(imagery, 'build_vrt_mosaic', lambda *args: {'path': str(mosaic)})
    output = tmp_path / 'acquisition'
    args = (bundle_dir, artifacts, catalog, 2023, ['va_fixture'], output)
    with pytest.raises(RuntimeError, match='interrupted'):
        fairfax_pilot.acquire_imagery(*args)
    assert not (output / 'acquisition.lock').exists()
    result = fairfax_pilot.acquire_imagery(*args)
    assert result['acquisition_date'] == '2023-10-11'
    assert (output / 'acquisition-receipt.json').exists()
    source.write_bytes(b'changed bytes')
    with pytest.raises(ValueError, match='checksum mismatch'):
        fairfax_pilot.acquire_imagery(*args)
    assert not (output / 'acquisition.lock').exists()


def test_species_coverage_keeps_calibration_jurisdictions_separate(tmp_path):
    manifest, taxonomy = bundle(tmp_path)
    inventory = pd.DataFrame([
        dict(tree_id='1', city='USARL', data_source='municipal', species='Acer rubrum',
             longitude=-77.1, latitude=38.9, plant_date=None, diameter_at_breast_height=12.),
        dict(tree_id='1', city='USALX', data_source='municipal', species='Quercus alba',
             longitude=-77.1, latitude=38.8, plant_date=None, diameter_at_breast_height=None)])
    path = tmp_path / 'calibration.parquet'
    inventory.to_parquet(path)
    manifest['calibration_inventory'] = path.name
    manifest['sha256'][path.name] = sha256(path)
    (tmp_path / 'manifest.json').write_text(json.dumps(manifest))
    groups = audit_bundle(tmp_path, taxonomy)['inventories']['calibration_inventory']['groups']
    assert {g['city']: g['coverage']['named_row_coverage'] for g in groups} == {'USALX': 0., 'USARL': 1.}
    inventory = pd.concat([inventory, inventory.iloc[:1]])
    inventory.to_parquet(path)
    manifest['sha256'][path.name] = sha256(path)
    (tmp_path / 'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='Duplicate'):
        audit_bundle(tmp_path, taxonomy)


def test_sampling_is_stable_geographic_and_preserves_test_blocks(tmp_path):
    cfg = config(tmp_path)
    # Use a metadata/mask raster double so this test doesn't allocate a county-size image.
    origin = from_origin(300000, 4320000, .3, .3)
    source = SimpleNamespace(crs='EPSG:32618', transform=origin, count=4, res=(.3, .3),
                             height=16000, width=16000,
                             read_masks=lambda bands, window: np.full((4, 512, 512), 255, dtype=np.uint8))
    region = box(300200, 4315500, 304500, 4319800)
    boundary = transform(Transformer.from_crs(source.crs, 'EPSG:4326', always_xy=True).transform, region)
    first = sample_windows(source, boundary, cfg, {'train': 8, 'validation': 4})
    assert first == sample_windows(source, boundary, cfg, {'train': 8, 'validation': 4})
    holdout = sample_windows(source, boundary, cfg, {'test': 2}, holdout_only=True)
    development_blocks = {(w['block_x'], w['block_y']) for w in first['windows']}
    for window in holdout['windows']:
        block = Block(window['block_x'], window['block_y'])
        assert split_for_block(block, cfg.split, cfg.seed) == window['split'] == 'test'
        assert (block.x, block.y) not in development_blocks
    for window in first['windows']:
        block = Block(window['block_x'], window['block_y'])
        assert split_for_block(block, cfg.split, cfg.seed) == window['split'] != 'test'
        left, top = origin * (window['col'], window['row'])
        footprint = box(left, top - 512 * .3, left + 512 * .3, top)
        assert region.buffer(.00001).covers(footprint)
        size, guard = cfg.split.block_size_m, cfg.split.guard_m
        assert box(block.x * size + guard, block.y * size + guard,
                   (block.x + 1) * size - guard, (block.y + 1) * size - guard).covers(footprint)
    with pytest.raises(ValueError, match='Insufficient'):
        sample_windows(source, boundary, cfg, {'train': 100000, 'validation': 1})


@pytest.mark.parametrize('explicit', [False, True])
def test_prediction_materialization_retains_unknown_supervision(tmp_path, explicit):
    cfg = config(tmp_path)
    source = tmp_path / 'rgbn.tif'
    with rasterio.open(source, 'w', driver='GTiff', width=1024, height=512, count=4,
                       dtype='uint8', crs='EPSG:32618', transform=from_origin(300000, 4320000, .3, .3)) as dst:
        dst.write(np.full((4, 512, 1024), 100, dtype=np.uint8))
    labels = tmp_path / 'labels.parquet'
    pd.DataFrame({'chip_id': ['old'], 'species_id': [1]}).to_parquet(labels)
    splits = {(0, 0): 'train', (0, 512): 'validation'} if explicit else None
    build_prediction_chips(cfg, source, [(0, 0), (0, 512)], labels_template=labels, window_splits=splits)
    root = tmp_path / 'chips' / cfg.dataset
    manifest = pd.read_parquet(root / 'chips.parquet')
    assert set(manifest.split) == ({'train', 'validation'} if explicit else {'train'})
    assert pd.read_parquet(root / 'labels.parquet').empty
    for path in manifest.path:
        with np.load(root / path) as chip:
            assert np.count_nonzero(chip['detection_mask']) == 0
            assert np.count_nonzero(chip['scoring_mask']) == 0
    assert json.loads((root / 'summary.json').read_text())['label_status'] == 'unavailable'
    with pytest.raises(ValueError, match='already exists'):
        build_prediction_chips(cfg, source, [(0, 0)], labels_template=labels)
    with pytest.raises(ValueError, match='train or validation'):
        build_prediction_chips(cfg, source, [(0, 0)], labels_template=labels, window_splits={(0, 0): 'test'})


def test_prepare_entry_point_writes_loadable_isolated_dataset(tmp_path, monkeypatch):
    import rasterio.shutil
    from urban_tree_ml import fairfax_pilot

    cfg = config(tmp_path)
    cfg.model.backbone = 'swin_tiny'
    artifacts = tmp_path / 'artifacts'
    run = artifacts / 'runs' / fairfax_pilot.PARENT
    (run / 'checkpoints').mkdir(parents=True)
    checkpoint = run / 'checkpoints' / '000-0000282.ckpt'
    checkpoint.write_bytes(b'fixture checkpoint')
    monkeypatch.setattr(fairfax_pilot, 'CHECKPOINT_SHA', sha256(checkpoint))
    (run / 'config-usbos.json').write_text(cfg.model_dump_json())
    taxonomy = run / 'vegetation-taxonomy.json'
    taxonomy.write_text(json.dumps({'species': ['Acer rubrum'], 'genera': ['Acer']}))
    parent_dataset = artifacts / 'chips' / f'{fairfax_pilot.PARENT}-usbos'
    parent_dataset.mkdir(parents=True)
    (parent_dataset / 'normalization.json').write_text(cfg.reference.normalization_path.read_text())
    pd.DataFrame({'chip_id': ['parent'], 'species_id': [0]}).to_parquet(parent_dataset / 'labels.parquet')
    bundle_dir = tmp_path / 'bundle'
    bundle_dir.mkdir()
    manifest, _ = bundle(bundle_dir)
    source = tmp_path / 'native.tif'
    with rasterio.open(source, 'w', driver='GTiff', width=7680, height=7680, count=4,
                       dtype='uint8', crs='EPSG:32618', transform=from_origin(300000, 4320000, .6, .6),
                       tiled=True, compress='deflate') as dst:
        for _, window in dst.block_windows(1):
            dst.write(np.full((4, int(window.height), int(window.width)), 100, dtype=np.uint8), window=window)
    boundary = transform(Transformer.from_crs('EPSG:32618', 'EPSG:4326', always_xy=True).transform,
                         box(300000, 4315392, 304608, 4320000))
    boundary_path = bundle_dir / 'boundary.geojson'
    boundary_path.write_text(json.dumps(mapping(boundary)))
    manifest['sha256']['boundary.geojson'] = sha256(boundary_path)
    (bundle_dir / 'manifest.json').write_text(json.dumps(manifest))
    vrt = tmp_path / 'native.vrt'
    rasterio.shutil.copy(source, vrt, driver='VRT')
    output = tmp_path / 'pilot'
    receipt = fairfax_pilot.prepare_pilot(bundle_dir, artifacts, vrt, '2023-10-11', output,
                                         {'train': 1, 'validation': 1})
    prepared = load_config(output / 'config.json')
    assert prepared.inventory.city == 'USFFX'
    assert prepared.split.projected_crs == 'EPSG:32618'
    assert prepared.paths.root == output
    assert prepared.imagery.source_resolution_m == .6
    assert receipt['dataset']['chips'] == 2
    assert receipt['source_files'][0]['sha256'] == sha256(source)
    assert (output / 'COMPLETE').exists()
    assert json.loads((output / 'sample-plan.json').read_text())['windows']
    from urban_tree_ml import evaluation
    calls = []
    def fake_evaluation(config, checkpoint_path, **kwargs):
        import torch
        assert 1 <= torch.get_num_threads() <= 2
        calls.append(kwargs)
        return {'prediction_only': True, 'ground_truth_trees': 0, 'chips': 1}
    monkeypatch.setattr(evaluation, 'run_evaluation', fake_evaluation)
    predictions = fairfax_pilot.predict_pilot(output)
    assert len(predictions['cohorts']) == 2
    assert [call['split'] for call in calls] == ['train', 'validation']
    inference = output / 'runs' / prepared.experiment
    assert all(call['device_name'] == 'cpu' and call['output_run'] == inference for call in calls)
    assert (inference / 'COMPLETE').exists()
    assert 1 <= json.loads((inference / 'inference-receipt.json').read_text())['cpu_threads'] <= 2
    with pytest.raises(FileExistsError):
        fairfax_pilot.predict_pilot(output)
    (output / 'config.json').write_text('{}')
    with pytest.raises(ValueError, match='input changed'):
        fairfax_pilot.predict_pilot(output)
    with pytest.raises(FileExistsError):
        fairfax_pilot.prepare_pilot(bundle_dir, artifacts, vrt, '2023-10-11', output,
                                    {'train': 1, 'validation': 1})


def test_sparse_mosaic_gap_is_excluded_even_when_gdal_marks_it_all_valid(tmp_path):
    from rasterio.windows import Window
    from urban_tree_ml.mosaic_coverage import missing_chips, source_rectangles, valid_window

    tif = tmp_path / 'tile.tif'
    with rasterio.open(tif, 'w', driver='GTiff', width=8, height=8, count=1,
                       dtype='uint8', transform=from_origin(5, 20, 1, 1)) as dst:
        dst.write(np.zeros((1, 8, 8), dtype=np.uint8))  # A photographed black pixel is valid.
    vrt = tmp_path / 'sparse.vrt'
    vrt.write_text("""<VRTDataset rasterXSize="16" rasterYSize="8"><GeoTransform>5,1,0,20,0,-1</GeoTransform>
        <VRTRasterBand dataType="Byte" band="1"><SimpleSource>
        <SourceFilename relativeToVRT="1">tile.tif</SourceFilename><SourceBand>1</SourceBand>
        <SrcRect xOff="0" yOff="0" xSize="8" ySize="8"/>
        <DstRect xOff="0" yOff="0" xSize="8" ySize="8"/>
        </SimpleSource></VRTRasterBand></VRTDataset>""")
    bounds = source_rectangles(vrt)
    with rasterio.open(vrt) as source:
        assert source.read_masks(1).min() == 255
        for x, fraction in [(0, 1), (4, .5), (8, 0)]:
            assert valid_window(source, [1], Window(x, 0, 8, 8), bounds).mean() == fraction
    ids = ['r000000_c000000', 'r000000_c000001']
    assert missing_chips(vrt, 8, ids) == {ids[1]}
    assert not missing_chips(tif, 8, ids[:1])


def test_review_coverage_rejects_partial_tiles_and_unions_adjacent_sources(monkeypatch):
    from urban_tree_ml import mosaic_coverage

    monkeypatch.setattr(mosaic_coverage, 'source_rectangles',
                        lambda path: ((0, 0, 6, 8), (4, 0, 12, 8)))
    # Overlapping sources cover the first tile fully and the second only halfway.
    ids = ['r000000_c000000', 'r000000_c000001']
    assert mosaic_coverage.missing_chips('mosaic.vrt', 8, ids) == {ids[1]}
    assert not mosaic_coverage.missing_chips('mosaic.vrt', 8, ids, .5)
