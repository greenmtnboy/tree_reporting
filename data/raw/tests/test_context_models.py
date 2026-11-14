"""Current context remains its own source pipeline, with two independent grains."""
from pathlib import Path
import tomllib
from datetime import date

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from trilogy import Environment, Dialects

DATA = Path(__file__).resolve().parents[2]


def test_context_models_parse_published_sources_without_managed_tree_targets():
    env = Environment(working_path=DATA/'context')
    env.parse((DATA/'context/usbos.preql').read_text(encoding='utf-8'))
    assert set(env.datasources) == {'usbos_surface_temperature', 'usbos_water'}
    heat, water = env.datasources['usbos_surface_temperature'], env.datasources['usbos_water']
    assert 'usbos_surface_temperature_v1.parquet' in str(heat.address)
    assert 'usbos_water_v1.parquet' in str(water.address)
    assert len(heat.grain.components) == len(water.grain.components) == 1
    assert heat.grain != water.grain


def test_context_source_job_is_independent_and_scheduled():
    jobs = tomllib.loads((DATA/'trilogy.toml').read_text(encoding='utf-8'))['cloud']['job']
    job = next(x for x in jobs if x['key'] == 'context-usbos')
    assert job['entrypoint'] == 'context_usbos.preql'
    assert job['operation'] == 'run'
    assert job['schedule'] == '0 0 5 2 * *'
    env = Environment(working_path=DATA)
    env.parse((DATA/job['entrypoint']).read_text(encoding='utf-8'))


def test_trilogy_reads_heat_dates_and_nullable_water_from_parquet(tmp_path):
    heat = tmp_path/'heat.parquet'
    water = tmp_path/'water.parquet'
    pq.write_table(pa.Table.from_pylist([{
        'observation_id': 'USBOS:2026-07-01:cell', 'cell_id': 'cell', 'city': 'USBOS',
        'acquired_on': date(2026, 7, 1), 'easting_m': 325015., 'northing_m': 4689985.,
        'longitude': -71.1, 'latitude': 42.3, 'surface_temperature_c': 31.,
        'valid_scene_count': 2, 'source': 'LANDSAT_C2_L2'}]), heat)
    pq.write_table(pa.Table.from_pylist([{'feature_id': 'USBOS:MASSGIS:7', 'city': 'USBOS',
        'source': 'MASSGIS_MASSDEP', 'source_feature_id': '7', 'name': None,
        'water_class': '1', 'geometry_wkt': 'POLYGON ((0 0, 1 0, 1 1, 0 0))'}]), water)
    env = Environment(working_path=DATA/'context')
    env.parse((DATA/'context/usbos.preql').read_text(encoding='utf-8'))
    executor = Dialects.DUCK_DB.default_executor(environment=env)
    for query, filename, local, expected in [
        ('select heat_acquired_on, surface_temperature_c, heat_valid_scene_count;',
         'usbos_surface_temperature_v1.parquet', heat, (date(2026, 7, 1), 31., 2)),
        ('select water_feature_id, water_name;', 'usbos_water_v1.parquet', water, ('USBOS:MASSGIS:7', None)),
    ]:
        sql, = executor.generate_sql(query)
        sql = sql.replace(f'https://storage.googleapis.com/trilogy_public_models/duckdb/context/{filename}', local.as_posix())
        assert 'https://' not in sql
        with duckdb.connect() as conn:
            assert conn.execute(sql).fetchall() == [expected]
