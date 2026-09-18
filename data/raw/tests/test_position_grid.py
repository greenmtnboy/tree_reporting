"""The position correction: raw/tree_position.preql, shared/overture.py, and
the wiring a city needs to take part.

Three things are silent if they drift.  The grid key is computed in two
places -- the `const`s the model derives `snap_cell` from and the same two
constants in shared/overture.py -- and a mismatch means every lookup misses
and no tree ever moves.  A city that imports tree_position without a staged
lookup builds with the join matching nothing.  And a city that neither
imports tree_position nor merges its own position has no
`latitude` at all, which the planner reports as an unresolvable target rather
than as the missing line.

The model's own SQL is run over fixture rows the way test_tree_predictions.py
does, so what is checked is what a refresh would publish.
"""

from __future__ import annotations

import math
import re
import sys
from pathlib import Path

import pyarrow as pa
import pytest

RAW_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = RAW_DIR.parent
sys.path.insert(0, str(RAW_DIR))

from shared.ingest import (  # noqa: E402
    CITY_BOUNDS,
    MUNICIPAL_DATA_SOURCES,
    SNAP_CELL_LAT_DEG,
    SNAP_CELL_LON_DEG,
    SNAP_CELL_LON_MULTIPLIER,
    snap_cell_centre,
    snap_cell_for,
    snap_cell_indices,
    snap_cell_key,
)
from shared.overture import (  # noqa: E402
    OVERTURE_SNAP_CITIES,
    SNAP_COLUMNS,
    SNAP_MAX_METRES,
    WIDE_SEARCH_BUCKET_CELLS,
    build_snap_table,
    cell_metres,
    release_published_at,
    snap_staging_name,
)
from test_cloud_jobs import jobs_by_key, statements  # noqa: E402
from test_data_sources import city_models  # noqa: E402

MODEL = RAW_DIR / "tree_position.preql"
DEDUP = RAW_DIR / "tree_dedup.preql"


# --- the grid ---------------------------------------------------------------


def model_constants() -> dict[str, float]:
    return {
        name: float(value)
        for name, value in re.findall(r"^const (\w+) <- ([-\d.]+);", MODEL.read_text(encoding="utf-8"), re.M)
    }


def test_the_model_and_the_ingest_agree_on_the_cell():
    """The one drift that silences the whole feature."""
    constants = model_constants()
    assert constants["snap_cell_lat_deg"] == SNAP_CELL_LAT_DEG
    assert constants["snap_cell_lon_deg"] == SNAP_CELL_LON_DEG


def test_key_packs_without_collision():
    """Both indices fit under the multiplier, negative longitudes included."""
    assert 180 / SNAP_CELL_LON_DEG < SNAP_CELL_LON_MULTIPLIER
    assert 90 / SNAP_CELL_LAT_DEG < SNAP_CELL_LON_MULTIPLIER / 2
    seen = set()
    for lat, lon in [(44.476, -73.212), (-33.9, 151.2), (60.2, 24.9), (4.6, -74.1), (0.0, 0.0), (-0.00001, -0.00001)]:
        key = snap_cell_for(lat, lon)
        assert key not in seen
        seen.add(key)
        i, j = snap_cell_indices(lat, lon)
        assert key == snap_cell_key(i, j) == i * SNAP_CELL_LON_MULTIPLIER + j
        assert abs(key) < 1e15
        # the centre lands back in the same cell
        assert snap_cell_indices(*snap_cell_centre(i, j)) == (i, j)


def test_the_cell_is_a_couple_of_metres_everywhere():
    for code in MUNICIPAL_DATA_SOURCES:
        height, width = cell_metres(code)
        assert 2.0 < height < 2.5
        assert 1.0 < width < 3.5, code


def test_the_wide_search_reaches_the_cap_everywhere():
    """A blocked cell within the cap of open ground must be able to find it."""
    for code in MUNICIPAL_DATA_SOURCES:
        assert WIDE_SEARCH_BUCKET_CELLS * min(cell_metres(code)) >= SNAP_MAX_METRES, code


def test_release_dates():
    assert release_published_at("2026-08-19.0").isoformat() == "2026-08-19T00:00:00+00:00"
    with pytest.raises(RuntimeError):
        release_published_at("latest")


# --- the staging query ------------------------------------------------------


@pytest.fixture(scope="module")
def spatial_duckdb():
    """A DuckDB with the spatial extension, or a skip where it cannot load.

    `INSTALL spatial` fetches the extension once per DuckDB version; a
    sandbox with no network and no cache is the one place this suite is not
    offline, and it skips rather than fails there.
    """
    import duckdb

    con = duckdb.connect()
    try:
        con.execute("INSTALL spatial; LOAD spatial;")
    except duckdb.Error as err:  # pragma: no cover - environment dependent
        pytest.skip(f"DuckDB spatial extension unavailable: {err}")
    return con


# A 30 x 20 m building and four roads, in Burlington's box.  The centre-
# inside rule, the class filter, the tunnel flag and a recorded width are
# each exercised by one of them.
BUILDING = "POLYGON((-73.21320 44.47691, -73.21282 44.47691, -73.21282 44.47709, -73.21320 44.47709, -73.21320 44.47691))"
ROADS = [
    ("LINESTRING(-73.2140 44.4760, -73.2100 44.4760)", "residential", False, None),
    ("LINESTRING(-73.2140 44.4750, -73.2100 44.4750)", "primary", True, None),   # a tunnel
    ("LINESTRING(-73.2140 44.4740, -73.2100 44.4740)", "footway", False, None),  # not a road here
    ("LINESTRING(-73.2140 44.4730, -73.2100 44.4730)", "service", False, 12.0),  # a recorded width
]


@pytest.fixture(scope="module")
def snap(spatial_duckdb) -> dict[int, dict]:
    buildings = f"SELECT ST_GeomFromText('{BUILDING}') AS geometry"
    segments = " UNION ALL ".join(
        f"SELECT ST_GeomFromText('{wkt}') AS geometry, '{cls}' AS class, "
        f"{'true' if tunnel else 'false'} AS is_tunnel, {width if width else 'NULL'}::DOUBLE AS width_m"
        for wkt, cls, tunnel, width in ROADS
    )
    table = build_snap_table(
        "USBTV", "2026-08-19.0", connection=spatial_duckdb, buildings=buildings, segments=segments
    )
    assert table.schema == pa.schema(SNAP_COLUMNS)
    return {row["snap_cell"]: row for row in table.to_pylist()}


def test_a_cell_inside_the_building_moves_just_outside_it(snap):
    row = snap[snap_cell_for(44.4770, -73.2130)]
    assert row["snap_reason"] == "building"
    assert row["city"] == "USBTV"
    assert row["overture_release"] == "2026-08-19.0"
    # 10 m to the nearest wall from the centre of a 20 m deep building, plus
    # the cell the target sits in
    assert 9.0 < row["snap_distance_m"] < 14.0
    assert not (44.47691 <= row["snap_latitude"] <= 44.47709 and -73.21320 <= row["snap_longitude"] <= -73.21282)


def test_a_cell_in_the_carriageway_moves_to_the_kerb(snap):
    row = snap[snap_cell_for(44.4760, -73.2120)]
    assert row["snap_reason"] == "road"
    assert 3.0 < row["snap_distance_m"] < 6.0   # a 3.5 m residential half width
    assert abs(row["snap_latitude"] - 44.4760) * 111320 > 3.5


def test_the_verge_is_open_ground(snap):
    assert snap_cell_for(44.4760 + 5 / 111320, -73.2120) not in snap


def test_tunnels_and_footways_do_not_block(snap):
    assert snap_cell_for(44.4750, -73.2120) not in snap
    assert snap_cell_for(44.4740, -73.2120) not in snap


def test_a_recorded_width_is_honoured(snap):
    # 5 m off the centreline is inside a 12 m carriageway, outside a 5 m one
    assert snap[snap_cell_for(44.4730 + 5 / 111320, -73.2120)]["snap_reason"] == "road"


def test_every_target_is_open_and_within_the_cap(snap):
    for row in snap.values():
        assert snap_cell_for(row["snap_latitude"], row["snap_longitude"]) not in snap
        assert 0 < row["snap_distance_m"] <= SNAP_MAX_METRES


# --- the model, over fixtures -----------------------------------------------

T_ROAD = (44.4760, -73.2120)
T_BUILDING = (44.4770, -73.2130)
T_OPEN = (44.4780, -73.2140)
T_SHARED = (44.4790, -73.2150)


def cell(point):
    return snap_cell_for(*point)


CITY_MODEL = """
import tree_common;
import community_tree_info;
import tree_position;

key tst_source enum<string>['TST_OPENDATA', 'COMMUNITY_TST', 'OSM_TST'];
auto tst_source_label <- concat(tst_source, '');
merge tst_source_label into source_label;

root partial datasource tst_raw_tree_info (
    tree_id: tree_id, city: city, data_source: tst_source, species: raw_species,
    tree_name: ?raw_tree_name, plant_date: ?raw_plant_date, latitude: ?raw_latitude,
    longitude: ?raw_longitude, diameter_at_breast_height: ?raw_dbh,
    submission_photo_url: ?raw_photo_url, cultivar: ?raw_cultivar,
)
grain (tree_id)
complete where city = 'USBTV' and tst_source = 'TST_OPENDATA'
query '''
SELECT * FROM (VALUES
    ('m-road',     'USBTV', 'TST_OPENDATA', 'Acer rubrum', NULL, NULL, {road_lat}, {road_lon}, 10.0, NULL, NULL),
    ('m-building', 'USBTV', 'TST_OPENDATA', 'Acer rubrum', NULL, NULL, {bld_lat}, {bld_lon}, 10.0, NULL, NULL),
    ('m-open',     'USBTV', 'TST_OPENDATA', 'Acer rubrum', NULL, NULL, {open_lat}, {open_lon}, 10.0, NULL, NULL),
    ('m-shared',   'USBTV', 'TST_OPENDATA', 'Acer rubrum', NULL, NULL, {shared_lat}, {shared_lon}, 10.0, NULL, NULL),
    ('m-nowhere',  'USBTV', 'TST_OPENDATA', 'Acer rubrum', NULL, NULL, NULL, NULL, 10.0, NULL, NULL)
) AS t(tree_id, city, data_source, species, tree_name, plant_date, latitude, longitude, diameter_at_breast_height, submission_photo_url, cultivar)
''';

root partial datasource tst_community_tree_info (
    tree_id: tree_id, city: city, data_source: tst_source, species: raw_species,
    tree_name: ?raw_tree_name, plant_date: ?raw_plant_date, diameter_at_breast_height: ?raw_dbh,
    latitude: ?raw_latitude, longitude: ?raw_longitude, submission_photo_url: ?raw_photo_url,
    cultivar: ?raw_cultivar,
)
grain (tree_id)
complete where city = 'USBTV' and tst_source = 'COMMUNITY_TST'
query '''
SELECT * FROM (VALUES
    ('c-shared', 'USBTV', 'COMMUNITY_TST', 'Acer saccharum', NULL, NULL, 12.0, {shared_lat}, {shared_lon}, 'http://p', NULL)
) AS t(tree_id, city, data_source, species, tree_name, plant_date, diameter_at_breast_height, latitude, longitude, submission_photo_url, cultivar)
''';

root partial datasource tst_osm_tree_info (
    tree_id: tree_id, city: city, data_source: tst_source, species: ?raw_species,
    tree_name: ?raw_tree_name, plant_date: ?raw_plant_date, latitude: ?raw_latitude,
    longitude: ?raw_longitude, diameter_at_breast_height: ?raw_dbh,
    submission_photo_url: ?raw_photo_url, cultivar: ?raw_cultivar,
)
grain (tree_id)
complete where city = 'USBTV' and tst_source = 'OSM_TST'
query '''
SELECT * FROM (VALUES
    ('o-open', 'USBTV', 'OSM_TST', 'Unknown', NULL, NULL, {osm_lat}, {osm_lon}, NULL, NULL, NULL)
) AS t(tree_id, city, data_source, species, tree_name, plant_date, latitude, longitude, diameter_at_breast_height, submission_photo_url, cultivar)
''';

root datasource tst_overture_snap (
    snap_cell: snap_cell, snap_latitude: snap_latitude, snap_longitude: snap_longitude,
    snap_reason: snap_reason, snap_distance_m: snap_distance_m,
)
grain (snap_cell)
query '''
SELECT * FROM (VALUES
    (CAST({road_cell} AS BIGINT),   44.47601, -73.21203, 'road',     2.9),
    (CAST({bld_cell} AS BIGINT),    44.47702, -73.21298, 'building', 3.1),
    (CAST({shared_cell} AS BIGINT), 44.47903, -73.21500, 'building', 3.4)
) AS t(snap_cell, snap_latitude, snap_longitude, snap_reason, snap_distance_m)
''';

merge merged_dbh into diameter_at_breast_height;
"""


@pytest.fixture(scope="module")
def published() -> dict[str, dict]:
    """What a refresh of the fixture city would publish, keyed by tree_id."""
    import duckdb
    from trilogy import Dialects, Environment

    osm = (T_OPEN[0] + 0.00001, T_OPEN[1] + 0.00001)  # same dedup cell as m-open
    model = CITY_MODEL.format(
        road_lat=T_ROAD[0], road_lon=T_ROAD[1], road_cell=cell(T_ROAD),
        bld_lat=T_BUILDING[0], bld_lon=T_BUILDING[1], bld_cell=cell(T_BUILDING),
        open_lat=T_OPEN[0], open_lon=T_OPEN[1],
        shared_lat=T_SHARED[0], shared_lon=T_SHARED[1], shared_cell=cell(T_SHARED),
        osm_lat=osm[0], osm_lon=osm[1],
    )
    env = Environment(working_path=RAW_DIR)
    env.parse(model)
    executor = Dialects.DUCK_DB.default_executor(environment=env)
    (sql,) = executor.generate_sql(
        """
        select
            tree_id, cluster_id, latitude, longitude,
            source_latitude, source_longitude, position_adjustment, position_shift_m,
            merged_sources
        where city = 'USBTV';
        """
    )
    for name, value in model_constants().items():
        sql = re.sub(rf":{name}\b", repr(value), sql)
    assert ":" not in re.sub(r"'[^']*'", "", sql), "an unbound parameter survived"
    conn = duckdb.connect()
    result = conn.execute(sql)
    columns = [d[0] for d in result.description]
    rows = [dict(zip(columns, row)) for row in result.fetchall()]
    # the prune every city target carries
    return {row["tree_id"]: row for row in rows if row["tree_id"] == row["cluster_id"]}


def close(a, b):
    return math.isclose(float(a), float(b), abs_tol=1e-6)


def test_a_tree_in_the_road_is_moved_and_its_origin_kept(published):
    row = published["m-road"]
    assert row["position_adjustment"] == "road"
    assert close(row["latitude"], 44.47601) and close(row["longitude"], -73.21203)
    assert close(row["source_latitude"], T_ROAD[0]) and close(row["source_longitude"], T_ROAD[1])
    assert close(row["position_shift_m"], 2.9)


def test_a_tree_in_a_building_is_moved(published):
    row = published["m-building"]
    assert row["position_adjustment"] == "building"
    assert close(row["latitude"], 44.47702)


def test_open_ground_is_untouched(published):
    row = published["m-open"]
    assert row["position_adjustment"] is None
    assert row["position_shift_m"] is None
    assert close(row["latitude"], T_OPEN[0]) and close(row["source_latitude"], T_OPEN[0])
    assert row["merged_sources"] == "OSM_TST,TST_OPENDATA"


def test_the_cluster_merge_still_picks_the_community_point(published):
    """The correction rides through @by_source: community first."""
    row = published["m-shared"]
    assert "COMMUNITY_TST" in row["merged_sources"]
    assert row["position_adjustment"] == "building"
    assert close(row["latitude"], 44.47903)
    assert "c-shared" not in published  # absorbed


def test_no_coordinates_stay_null(published):
    row = published["m-nowhere"]
    assert row["latitude"] is None and row["source_latitude"] is None
    assert row["position_adjustment"] is None


# --- wiring ----------------------------------------------------------------


def test_the_dedup_model_no_longer_claims_position():
    """Two merges into one concept conflict, so the policy is the city's."""
    text = statements(DEDUP)
    assert "merge merged_latitude into latitude;" not in text
    assert "merge merged_longitude into longitude;" not in text


@pytest.mark.parametrize("code", sorted(MUNICIPAL_DATA_SOURCES))
def test_every_city_has_exactly_one_position_policy(code: str):
    text = statements(city_models()[code])
    imports = "import ..tree_position;" in text
    merges = "merge merged_latitude into latitude;" in text and "merge merged_longitude into longitude;" in text
    assert imports != merges, (
        f"{code} must either import tree_position or merge merged_latitude and "
        "merged_longitude itself, and not both"
    )
    assert imports == (code in OVERTURE_SNAP_CITIES), (
        f"{code}: importing tree_position and OVERTURE_SNAP_CITIES must agree, or "
        "the lookup is either never built or never read"
    )


@pytest.mark.parametrize("code", sorted(OVERTURE_SNAP_CITIES))
def test_a_snap_city_is_fully_wired(code: str):
    lower = code.lower()
    assert code in MUNICIPAL_DATA_SOURCES and code in CITY_BOUNDS
    model = city_models()[code]
    text = statements(model)

    # the cell is derived in tree_position.preql from the row's own
    # coordinates; a partition that maps it as a column is reading a stale
    # stamped value from before the derivation moved into the model
    partitions = re.findall(r"root partial datasource \w+ \((.*?)\)\s*grain \(tree_id\)", text, re.S)
    assert partitions, f"{code} declares no raw partitions"
    for body in partitions:
        assert "snap_cell" not in body, f"{code}: a raw partition still maps snap_cell as a column"

    assert f"root datasource {lower}_overture_snap (" in text
    assert snap_staging_name(code) in text
    for column in ("snap_latitude", "snap_longitude", "snap_reason", "snap_distance_m"):
        assert f"{column}: {column}," in text

    # the probe, and the watermark that makes a new lookup rebuild the city
    probe = f"{lower}_overture_data_updated_through"
    assert f"data_updated_through: {probe}" in text
    assert f"file `./{model.stem.removesuffix('_tree_info')}_overture_probe.py`" in text
    assert probe in text.split("greatest(", 1)[1].split(")", 1)[0]
    assert f"property <*>.{probe} datetime;" in statements(RAW_DIR / "tree_common.preql")
    assert (model.parent / f"{model.stem.removesuffix('_tree_info')}_overture_probe.py").exists()
    assert (model.parent / f"{model.stem.removesuffix('_tree_info')}_overture_extract.py").exists()

    # the published columns
    target = text.split(f"partial datasource {model.stem} (", 1)[1].split(")", 1)[0]
    for column in ("?source_latitude", "?source_longitude", "?position_adjustment", "?position_shift_m"):
        assert column in target, f"{code}'s target does not publish {column}"

    # the staging model and its job
    staging = DATA_DIR / "overture_staging" / f"{lower}_overture_staging.preql"
    assert staging.exists(), f"{code} has no Overture staging model"
    staging_text = statements(staging)
    assert "file `./overture_snap_rows.py`" in staging_text
    assert f"where city = '{code}';" in staging_text, f"{code}'s staging model does not push its city down"
    assert snap_staging_name(code) in staging_text
    assert "freshness by overture_released_through;" in staging_text
    job = jobs_by_key().get(f"overture-{code.lower()}")
    assert job, f"{code} has no overture-{lower} job; nothing would ever rebuild its lookup"
    assert job["entrypoint"] == f"overture_staging/{lower}_overture_staging.preql"
    assert job["operation"] == "refresh"
    assert job.get("schedule"), f"overture-{lower} needs a cron: the release watermark is what gates the rebuild"


def test_every_staging_model_is_a_snap_city():
    listed = {p.stem.removesuffix("_overture_staging").upper() for p in (DATA_DIR / "overture_staging").glob("*_overture_staging.preql")}
    assert listed == set(OVERTURE_SNAP_CITIES)
