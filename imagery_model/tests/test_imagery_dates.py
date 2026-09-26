import json
from types import SimpleNamespace

import pytest

from urban_tree_ml.imagery_dates import resolve_planting_cutoff


def config(required=True):
    return SimpleNamespace(planting_date_cutoff=None, require_acquisition_date=required)


def test_selected_sources_only_and_mosaic_latest_day(tmp_path):
    directory = tmp_path / '2023'
    directory.mkdir()
    raster = directory / 'mosaic.vrt'
    raster.with_suffix('.manifest.json').write_text(json.dumps({'sources': [{'item_id': 'a'}, {'item_id': 'b'}]}))
    (tmp_path / 'stac-items.json').write_text(json.dumps({'items': [
        {'id': 'a', 'datetime': '2023-07-06T16:00:00Z'},
        {'id': 'b', 'datetime': '2023-07-07T16:00:00Z'},
        {'id': 'not-selected', 'datetime': '2023-09-01T16:00:00Z'}]}))
    result = resolve_planting_cutoff(config(), raster)
    assert result['cutoff'] == '2023-07-07'
    assert len(result['metadata_sha256']) == 2


def test_single_asset_and_missing_source(tmp_path):
    raster = tmp_path / 'asset.tif'
    manifest = raster.with_suffix('.manifest.json')
    manifest.write_text(json.dumps({'acquisition_datetime': '2022-05-18T16:00:00Z'}))
    assert resolve_planting_cutoff(config(), raster)['cutoff'] == '2022-05-18'
    manifest.write_text(json.dumps({'sources': [{'item_id': 'missing'}]}))
    with pytest.raises(ValueError, match='Missing acquisition date'):
        resolve_planting_cutoff(config(), raster)


def test_missing_manifest_requires_explicit_date_when_enabled(tmp_path):
    with pytest.raises(ValueError, match='Missing acquisition manifest'):
        resolve_planting_cutoff(config(), tmp_path / 'image.tif')
    assert resolve_planting_cutoff(config(False), tmp_path / 'image.tif')['cutoff'] is None
