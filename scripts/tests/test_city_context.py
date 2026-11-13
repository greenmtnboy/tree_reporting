"""Source normalization, missingness and safe replacement of current city assets."""
from datetime import date
import json
from pathlib import Path
import sys
from unittest.mock import Mock, patch

import numpy as np
import pyarrow.parquet as pq
import pytest
from rasterio.transform import from_origin
from shapely.geometry import box, mapping

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'data/context'))
from config import CITIES, PREFIX, filenames, latest_summer
from sources import clear_land_mask, mosaic, heat_table, write_water, catalog, build_water
from refresh import digest, publish, validate


def seed(tmp_path):
    heat, water, meta = filenames('USBOS')
    table = heat_table('USBOS', '2026-07-01', np.array([[30., np.nan]], dtype='float32'),
                       np.array([[2, 0]], dtype='uint16'), from_origin(325000, 4690000, 30, 30), 'EPSG:32619')
    pq.write_table(table, tmp_path/heat)
    write_water(tmp_path/water, 'USBOS', [{'properties': {'OBJECTID': 7, 'NAME': None, 'POLY_CODE': 1},
                                         'geometry': mapping(box(-71.1, 42.3, -71.09, 42.31))}])
    metadata = {'schema_version': 1, 'city': 'USBOS', 'generated_at': '2026-11-13T12:00:00+00:00',
        'heat': {'year': 2026, 'rows': 1}, 'water': {'rows': 1},
        'assets': {name: {'sha256': digest(tmp_path/name), 'bytes': (tmp_path/name).stat().st_size}
                   for name in (heat, water)}}
    return metadata


def test_latest_completed_summer_and_schema_paths():
    assert latest_summer(date(2026, 8, 31)) == 2025
    assert latest_summer(date(2026, 9, 1)) == 2026
    assert latest_summer(date(2027, 1, 1)) == 2026
    assert filenames('USBOS') == ('usbos_surface_temperature_v1.parquet', 'usbos_water_v1.parquet', 'usbos_context_v1.json')
    with pytest.raises(ValueError):
        filenames('../escape')


def test_heat_qa_and_same_day_mosaic_keep_missing_values():
    valid = clear_land_mask(np.full(8, 30000), np.array([0, 8, 128, 512, 0, 0, 0, 0]),
                            np.array([300, 100, 100, 100, 301, 0, 100, 100]),
                            np.array([100, 100, 100, 100, 100, 100, 99, 101]))
    assert valid.tolist() == [True, False, False, False, False, False, False, True]
    result, count = mosaic([np.array([[30., np.nan, np.nan]]), np.array([[32., 20., np.nan]])])
    assert result[0, :2].tolist() == [31., 20.]
    assert np.isnan(result[0, 2])
    assert count.tolist() == [[2, 1, 0]]


def test_heat_rows_are_pixel_centers_and_dates_not_poster_averages(tmp_path):
    seed(tmp_path)
    table = pq.read_table(tmp_path/filenames('USBOS')[0])
    assert table.num_rows == 1
    assert table['easting_m'].to_pylist() == [325015.]
    assert table['northing_m'].to_pylist() == [4689985.]
    assert table['acquired_on'].to_pylist() == [date(2026, 7, 1)]
    assert table['valid_scene_count'].to_pylist() == [2]


def test_fully_clouded_dates_keep_same_parquet_schema(tmp_path):
    args = ('USBOS', '2026-07-01')
    transform = from_origin(325000, 4690000, 30, 30)
    empty = heat_table(*args, np.full((1, 2), np.nan, dtype='float32'),
                       np.zeros((1, 2), dtype='uint16'), transform, 'EPSG:32619')
    valid = heat_table(*args, np.full((1, 2), 30., dtype='float32'),
                       np.ones((1, 2), dtype='uint16'), transform, 'EPSG:32619')
    assert empty.schema == valid.schema
    with pq.ParquetWriter(tmp_path/'mixed.parquet', empty.schema) as writer:
        writer.write_table(empty)
        writer.write_table(valid)
    assert pq.read_table(tmp_path/'mixed.parquet').num_rows == 2


def test_water_keeps_geometry_source_identity_and_nullable_name(tmp_path):
    seed(tmp_path)
    table = pq.read_table(tmp_path/filenames('USBOS')[1])
    assert table['feature_id'].to_pylist() == ['USBOS:MASSGIS:7']
    assert table['name'].to_pylist() == [None]
    assert json.loads(table.schema.metadata[b'geo'])['columns']['geometry']['crs']['id']['code'] == 4326


def test_missing_year_is_not_silently_replaced_with_old_heat():
    with patch('sources.get_json', return_value={'features': [], 'links': []}):
        with pytest.raises(ValueError, match='No usable'):
            catalog(CITIES['USBOS'], 2026)


def test_water_truncation_refuses_incomplete_shoreline(tmp_path):
    with patch('sources.get_json', side_effect=[{}, {'objectIds': [1, 2]}, {'features': [], 'exceededTransferLimit': True}]):
        with pytest.raises(ValueError, match='Truncated'):
            build_water('USBOS', CITIES['USBOS'], tmp_path/'water.parquet')


def test_publish_stable_keys_generation_checks_metadata_last(tmp_path):
    metadata = seed(tmp_path)
    client = Mock()
    client.put.side_effect = [100, 101, 102]
    names = filenames('USBOS')
    generations = {f'{PREFIX}/{n}': i+1 for i, n in enumerate(names)}
    url = publish(tmp_path, 'USBOS', metadata, client, generations)
    assert url.endswith('/duckdb/context/usbos_context_v1.json')
    assert [c.args[1] for c in client.put.call_args_list] == [f'{PREFIX}/{n}' for n in names]
    assert [c.kwargs['if_generation_match'] for c in client.put.call_args_list] == [1, 2, 3]
    final = json.loads(client.put.call_args_list[-1].args[2])
    assert final['assets'][names[0]]['generation'] == 100
    assert len(final['assets']) == 2


def test_failed_asset_upload_never_writes_metadata(tmp_path):
    metadata = seed(tmp_path)
    client = Mock()
    client.put.side_effect = [100, RuntimeError('upload failure')]
    with pytest.raises(RuntimeError):
        publish(tmp_path, 'USBOS', metadata, client, {})
    assert client.put.call_count == 2
    assert all(not c.args[1].endswith('.json') for c in client.put.call_args_list)


@pytest.mark.parametrize('change', ['older_year', 'heat_loss', 'water_loss', 'older_build'])
def test_regressions_do_not_replace_current_data(tmp_path, change):
    metadata = seed(tmp_path)
    previous = json.loads(json.dumps(metadata))
    previous['generated_at'] = '2026-11-12T12:00:00+00:00'
    if change == 'older_year':
        previous['heat']['year'] = 2027
    elif change == 'heat_loss':
        previous['heat']['rows'] = 2
    elif change == 'water_loss':
        previous['water']['rows'] = 2
    else:
        previous['generated_at'] = '2026-11-14T12:00:00+00:00'
    client = Mock()
    with pytest.raises(ValueError):
        publish(tmp_path, 'USBOS', metadata, client, {}, previous)
    client.put.assert_not_called()


def test_duplicate_observations_fail_validation(tmp_path):
    import pyarrow as pa
    metadata = seed(tmp_path)
    name = filenames('USBOS')[0]
    table = pq.read_table(tmp_path/name)
    pq.write_table(pa.concat_tables([table, table]), tmp_path/name)
    metadata['heat']['rows'] = 2
    metadata['assets'][name] = {'sha256': digest(tmp_path/name), 'bytes': (tmp_path/name).stat().st_size}
    with pytest.raises(ValueError, match='keys'):
        validate(tmp_path, 'USBOS', metadata)


def test_corrupt_second_asset_fails_before_any_upload(tmp_path):
    metadata = seed(tmp_path)
    (tmp_path/filenames('USBOS')[1]).write_bytes(b'corrupt water')
    client = Mock()
    with pytest.raises(ValueError, match='checksum'):
        publish(tmp_path, 'USBOS', metadata, client, {})
    client.put.assert_not_called()
