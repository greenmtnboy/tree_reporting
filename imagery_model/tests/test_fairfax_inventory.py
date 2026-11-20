import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from pyproj import Transformer
from rasterio.transform import from_origin
from shapely.geometry import box
from shapely.ops import transform

from urban_tree_ml.config import load_config
from urban_tree_ml.fairfax_curation import development_plan, admitted_ids
from urban_tree_ml.fairfax_inventory import inventory_windows, reference_labels
from urban_tree_ml.fairfax_pilot import sample_windows, sha256
from urban_tree_ml.review_ranking import calculate


def test_inventory_windows_keep_sources_dates_and_test_separation(tmp_path):
    config = load_config(Path(__file__).parents[1] / 'configs/sf_naip_smoke.yaml')
    config.imagery.chip_pixels = 512
    config.imagery.resolution_m = .3
    config.imagery.planting_date_cutoff = date(2023, 10, 11)
    config.split.projected_crs = 'EPSG:32618'
    source = SimpleNamespace(crs='EPSG:32618', transform=from_origin(300000, 4320000, .3, .3),
                             count=4, res=(.3, .3), width=16000, height=16000,
                             read_masks=lambda bands, window: np.full((4, 512, 512), 255, dtype=np.uint8))
    to_geo = Transformer.from_crs(source.crs, 4326, always_xy=True).transform
    boundary = transform(to_geo, box(300200, 4315500, 304500, 4319800))
    windows = sample_windows(source, boundary, config, {'train': 1, 'validation': 1})['windows']
    windows += sample_windows(source, boundary, config, {'test': 1}, holdout_only=True)['windows']
    records = []
    for window in windows:
        longitude, latitude = to_geo(*(source.transform * (window['col'] + 256, window['row'] + 256)))
        records.append(dict(tree_id=window['split'], longitude=longitude, latitude=latitude,
                            city='USFCH', data_source='FALLS_CHURCH', plant_date=None))
    records.append(dict(records[0], tree_id='new-planting', plant_date='2024-01-01'))
    inventory, planned, counts = inventory_windows(pd.DataFrame(records), boundary, source, config)
    assert set(inventory.tree_id) == {'train', 'validation'}
    assert set(inventory.city) == {'USFCH'}
    assert set(inventory.data_source) == {'FALLS_CHURCH'}
    assert {w['split'] for w in planned} == {'train', 'validation'}
    assert counts['test_block'] == counts['planted_after_imagery'] == 1
    # County-located points can be reviewed in a whole border tile. County
    # boundaries do not turn neighboring image pixels into another data split.
    first = records[0]
    narrow_boundary = box(first['longitude'] - .00001, first['latitude'] - .00001,
                          first['longitude'] + .00001, first['latitude'] + .00001)
    result, _, _ = inventory_windows(pd.DataFrame(records[:1]), narrow_boundary, source, config)
    assert result.tree_id.tolist() == ['train']


def test_extensions_preserve_original_plan_and_require_curated_completion(tmp_path):
    base = dict(windows=[dict(row=0, col=0, split='train')], seed=17, split_policy={'block_size_m': 768})
    path = tmp_path / 'sample-plan.json'
    path.write_text(json.dumps(base))
    inventory = tmp_path / 'inventory.parquet'
    inventory.write_bytes(b'fixture')
    extensions = tmp_path / 'development-extensions'
    extensions.mkdir()
    extension = dict(base_plan_sha256=sha256(path), inventory_sha256=sha256(inventory),
                     seed=17, split_policy=base['split_policy'], windows=[dict(row=512, col=0, split='validation')])
    target = extensions / 'inventory.json'
    target.write_text(json.dumps(extension))
    merged = development_plan(tmp_path, inventory)
    assert len(merged['windows']) == 2
    assert json.loads(path.read_text()) == base
    manifest = {'scenes': [dict(scene_id='new', validation_chip_id='r000001_c000000',
                                image_width=512, image_height=512, splits=['validation'])]}
    assert admitted_ids(manifest, {'scene_reviews': {'new': {'done': True}}}, merged)['validation'] == []
    assert admitted_ids(manifest, {'scene_reviews': {'new': {'more_done': True}}}, merged)['validation'] == ['r000001_c000000']
    extension['windows'][0]['split'] = 'test'
    target.write_text(json.dumps(extension))
    with pytest.raises(ValueError, match='test windows'):
        development_plan(tmp_path, inventory)
    inventory.write_bytes(b'changed')
    with pytest.raises(ValueError, match='frozen inputs'):
        development_plan(tmp_path, inventory)


def test_reference_labels_rank_misses_and_ignore_unknown_background():
    config = SimpleNamespace(imagery=SimpleNamespace(resolution_m=.3), targets=SimpleNamespace(output_stride=2))
    inventory = pd.DataFrame([dict(tree_id=str(i), chip_id='chip', split='train', longitude=0., latitude=0.,
                                  pixel_col=x, pixel_row=20., column_offset=0, row_offset=0, dbh_log1p=np.nan,
                                  genus_id=-1, species_id=-1, city='USARL', data_source='ARLINGTON')
                              for i, x in enumerate([20., 100.])])
    truth = reference_labels(inventory, config)
    assert truth.source_city.tolist() == ['USARL', 'USARL']
    predictions = pd.DataFrame([dict(chip_id='chip', output_x=x, output_y=10., species_id=0,
                                      score=.5, detection_mask_value=0.) for x in [10., 200.]])
    bundle = SimpleNamespace(config=config, chip_ids=['chip'], ground_truth=truth,
                             predictions=predictions, metrics={'prediction_only': False})
    row = calculate(bundle, .13, 2)[0]
    assert row['matched'] == row['missed'] == row['ignored_predictions'] == 1
    assert row['false_positive'] == 0
