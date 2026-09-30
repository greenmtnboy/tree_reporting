import sys
import tomllib
from pathlib import Path

import duckdb
import pyarrow as pa

RAW = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAW))
from tree_rankings import build_rankings


def test_city_frequency_boundaries_sentinels_and_tied_measurements():
    rows = []
    species = ['Rare species'] + ['Unusual species'] * 5 + ['Common species'] * 94
    for i, name in enumerate(species + ['Unknown', 'Palm', 'Shrub', 'Dead', 'Cactus']):
        rows.append(dict(tree_id=f'a-{i}', city='A', species=name,
                         latitude=42., longitude=-71.,
                         diameter_at_breast_height=40. if i < 2 else 0.,
                         crown_width_m=20. if i < 2 else None))
    rows.append(dict(tree_id='b-1', city='B', species='Rare species', latitude=1., longitude=2.,
                     diameter_at_breast_height=float('nan'), crown_width_m=-1.))
    with duckdb.connect() as con:
        con.register('trees', pa.Table.from_pylist(rows))
        con.register('enrichment', pa.table({'species': ['Rare species'], 'tree_form': ['conifer']}))
        result = {r['ranking_tree_id']: r for r in build_rankings(con).to_pylist()}
    assert result['a-0']['rarity_tier'] == 'rare'
    assert result['a-0']['species_city_share'] == .01
    assert result['a-1']['rarity_tier'] == 'unusual'
    assert result['a-1']['species_city_share'] == .05
    assert result['a-6']['rarity_tier'] == 'common'
    assert result['a-0']['trunk_rank'] == result['a-1']['trunk_rank'] == 1
    assert result['a-0']['canopy_rank'] == result['a-1']['canopy_rank'] == 1
    assert result['a-2']['trunk_rank'] is None
    assert result['b-1']['rarity_tier'] == 'common'
    assert result['b-1']['trunk_rank'] is None
    assert result['b-1']['canopy_rank'] is None
    for i in range(100, 105):
        assert result[f'a-{i}']['rarity_tier'] is None
        assert result[f'a-{i}']['species_city_count'] is None
    assert len(result) == len(rows)


def test_rankings_job_and_input_edges():
    config = tomllib.loads((RAW.parent / 'trilogy.toml').read_text())
    job = next(j for j in config['cloud']['job'] if j['key'] == 'refresh-rankings')
    assert job['entrypoint'] == 'raw/tree_rankings.preql'
    assert job['schedule'] == '0 0 6 * * *'
    assert set(config['dependencies'][job['entrypoint']]['after']) == {
        'raw/full_tree_publish.preql', 'raw/tree_enrichment.preql'}


def test_older_rollup_without_crown_width_still_produces_trunk_and_rarity():
    with duckdb.connect() as con:
        con.execute("""CREATE VIEW trees AS SELECT 'a' AS tree_id, 'USBOS' AS city,
            'Quercus rubra' AS species, 42.0 AS latitude, -71.0 AS longitude,
            20.0 AS diameter_at_breast_height""")
        con.register('enrichment', pa.table({'species': ['Quercus rubra'], 'tree_form': ['broadleaf']}))
        row = build_rankings(con).to_pylist()[0]
    assert row['canopy_rank'] is None
    assert row['trunk_rank'] == 1
    assert row['rarity_tier'] == 'common'
