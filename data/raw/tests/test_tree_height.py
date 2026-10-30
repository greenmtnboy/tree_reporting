"""Tree height from LIDAR canopy surveys: raw/tree_height.preql, shared/canopy.py,
and the wiring a city needs to take part.

The same three silent drifts as the position correction (test_position_grid.py):
the grid constants in two places, a city that imports the model with no
staged table, and a staging model with no job.  Plus the two rules that are
this feature's own: the rasteriser gives each cell to one crown, and an
absorbed prediction lends its height where nothing else in the cluster found
a crown.

The model's own SQL is run over fixture rows, so what is checked is what a
refresh would publish.
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

from shared.canopy import (  # noqa: E402
    CANOPY_LAYERS,
    CROWN_SCHEMA,
    RADIUS_MAX_M,
    RADIUS_MIN_M,
    build_height_table,
    height_staging_name,
)
from shared.ingest import SNAP_CELL_LAT_DEG, SNAP_CELL_LON_DEG, snap_cell_for  # noqa: E402
from test_cloud_jobs import jobs_by_key, statements  # noqa: E402
from test_data_sources import city_models, published_target, raw_partitions, stored  # noqa: E402

MODEL = RAW_DIR / "tree_height.preql"

# Boston, a block of Commonwealth Ave; metres to degrees at this latitude.
LAT0, LON0 = 42.35, -71.08
M_LAT = 1 / 111320.0
M_LON = 1 / (111320.0 * math.cos(math.radians(LAT0)))


def at(east_m: float, north_m: float) -> tuple[float, float]:
    """(lat, lon) a given offset from the origin."""
    return LAT0 + north_m * M_LAT, LON0 + east_m * M_LON


def model_constants() -> dict[str, float]:
    return {
        name: float(value)
        for name, value in re.findall(r"^const (\w+) <- ([-\d.]+);", MODEL.read_text(encoding="utf-8"), re.M)
    }


def test_the_model_and_the_builder_agree_on_the_cell():
    constants = model_constants()
    assert constants["canopy_cell_lat_deg"] == SNAP_CELL_LAT_DEG
    assert constants["canopy_cell_lon_deg"] == SNAP_CELL_LON_DEG


# --- the rasteriser -----------------------------------------------------------


def crowns(*rows) -> pa.Table:
    """(id, east_m, north_m, height_m, radius_m) -> a CROWN_SCHEMA table."""
    cols = {name: [] for name in CROWN_SCHEMA.names}
    for crown_id, east, north, height, radius in rows:
        lat, lon = at(east, north)
        cols["crown_id"].append(crown_id)
        cols["crown_source"].append("TEST_LIDAR")
        cols["latitude"].append(lat)
        cols["longitude"].append(lon)
        cols["height_m"].append(height)
        cols["radius_m"].append(radius)
    return pa.table(cols, schema=CROWN_SCHEMA)


@pytest.fixture(scope="module")
def raster() -> dict[int, dict]:
    table = build_height_table(
        "USBOS",
        crowns(
            ("big", 0, 0, 20.0, 6.0),
            # a small crown whose centroid sits inside the big one's circle
            ("small", 4, 0, 8.0, 2.0),
            ("far", 50, 0, 12.0, 3.0),
            ("tiny", 100, 0, 6.0, 0.2),
            ("stand", 200, 0, 25.0, 60.0),
            ("hedge", 300, 0, 1.0, 3.0),
        ),
    )
    return {row["canopy_cell"]: row for row in table.to_pylist()}


def crown_at(raster, east, north):
    row = raster.get(snap_cell_for(*at(east, north)))
    return row and row["canopy_crown_id"]


def test_a_point_under_a_crown_gets_its_height(raster):
    row = raster[snap_cell_for(*at(-3, 1))]
    assert row["canopy_crown_id"] == "big" and row["canopy_height_m"] == 20.0


def test_open_ground_has_no_row(raster):
    assert crown_at(raster, 25, 0) is None


def test_overlap_goes_to_the_relatively_nearer_crown(raster):
    # 4.2 m from big (0.70 of its radius) and 0.2 m from small: small
    assert crown_at(raster, 4.2, 0) == "small"
    # 2.5 m from big (0.42) and 1.5 m from small (0.75): big, though small's centroid is nearer
    assert crown_at(raster, 2.5, 0) == "big"


def test_a_tiny_crown_still_covers_its_neighbourhood(raster):
    assert crown_at(raster, 100 + RADIUS_MIN_M * 0.6, 0) == "tiny"


def test_a_merged_stand_is_capped(raster):
    assert crown_at(raster, 200 + RADIUS_MAX_M * 0.8, 0) == "stand"
    assert crown_at(raster, 200 + RADIUS_MAX_M * 1.5, 0) is None


def test_implausible_heights_are_dropped(raster):
    assert crown_at(raster, 300, 0) is None


def test_one_row_per_cell(raster):
    assert len(raster) == len(set(raster))


# --- the model, over fixture rows -------------------------------------------

CITY_MODEL = """
import tree_common;
import tree_height;

key tst_source enum<string>['TST_OPENDATA', 'OSM_TST', 'PREDICTED_TST'];
auto tst_source_label <- concat(tst_source, '');
merge tst_source_label into source_label;

root partial datasource tst_raw_tree_info extends tree_partition (data_source: tst_source)
complete where city = 'USBOS' and tst_source = 'TST_OPENDATA'
query '''
SELECT {pad} FROM (VALUES
    ('m-under',    'USBOS', 'TST_OPENDATA', 'Acer rubrum', {under_lat}, {under_lon}),
    ('m-kerb',     'USBOS', 'TST_OPENDATA', 'Acer rubrum', {kerb_lat}, {kerb_lon}),
    ('m-open',     'USBOS', 'TST_OPENDATA', 'Acer rubrum', {open_lat}, {open_lon}),
    ('m-nowhere',  'USBOS', 'TST_OPENDATA', 'Acer rubrum', NULL, NULL)
) AS v(tree_id, city, data_source, species, latitude, longitude)
''';

root partial datasource tst_osm_tree_info extends tree_partition (data_source: tst_source, species: ?raw_species)
complete where city = 'USBOS' and tst_source = 'OSM_TST'
query '''
SELECT {pad} FROM (VALUES
    ('o-alone', 'USBOS', 'OSM_TST', 'Unknown', {osm_lat}, {osm_lon})
) AS v(tree_id, city, data_source, species, latitude, longitude)
''';

root partial datasource tst_predicted_tree_info extends tree_partition (data_source: tst_source, species: ?raw_species)
complete where city = 'USBOS' and tst_source = 'PREDICTED_TST'
query '''
SELECT {pad} FROM (VALUES
    -- absorbed by m-kerb (same dedup cell), but on the crown m-kerb misses
    ('p-kerb',  'USBOS', 'PREDICTED_TST', 'Acer', {pkerb_lat}, {pkerb_lon}),
    ('p-alone', 'USBOS', 'PREDICTED_TST', 'Acer', {palone_lat}, {palone_lon})
) AS v(tree_id, city, data_source, species, latitude, longitude)
''';

root datasource tst_canopy_height (
    canopy_cell: canopy_cell, canopy_height_m: canopy_height_m, canopy_crown_id: canopy_crown_id,
    canopy_crown_radius_m: canopy_crown_radius_m, canopy_source: canopy_source,
)
grain (canopy_cell)
query '''
SELECT * FROM (VALUES
    (CAST({under_cell} AS BIGINT), 15.5, 'TEST-1', 4.0, 'TEST_LIDAR'),
    (CAST({pkerb_cell} AS BIGINT), 11.0, 'TEST-2', 3.0, 'TEST_LIDAR'),
    (CAST({osm_cell} AS BIGINT),    9.0, 'TEST-3', 3.0, 'TEST_LIDAR'),
    (CAST({palone_cell} AS BIGINT), 7.5, 'TEST-4', 2.0, 'TEST_LIDAR')
) AS v(canopy_cell, canopy_height_m, canopy_crown_id, canopy_crown_radius_m, canopy_source)
''';

merge merged_dbh into diameter_at_breast_height;
merge merged_latitude into latitude;
merge merged_longitude into longitude;
"""


# Every column tree_partition binds; the fixtures carry only the ones under test.
PAD = (
    "tree_id, city, data_source, species, NULL::VARCHAR AS tree_name, NULL::DATE AS plant_date, "
    "latitude, longitude, NULL::DOUBLE AS diameter_at_breast_height, NULL::VARCHAR AS submission_photo_url, "
    "NULL::VARCHAR AS cultivar, NULL::DOUBLE AS crown_width_m"
)


@pytest.fixture(scope="module")
def published() -> dict[str, dict]:
    import duckdb
    from trilogy import Dialects, Environment
    from trilogy.dialect.config import DuckDBConfig

    points = {
        "under": at(0, 0),
        "kerb": at(40, 0),
        # 1.5 m from m-kerb: inside one 10 m dedup cell, a different 2 m canopy cell
        "pkerb": at(41.5, 0),
        "open": at(80, 0),
        "osm": at(120, 0),
        "palone": at(160, 0),
    }
    fmt = {"pad": PAD}
    for name, (lat, lon) in points.items():
        fmt[f"{name}_lat"], fmt[f"{name}_lon"], fmt[f"{name}_cell"] = lat, lon, snap_cell_for(lat, lon)
    env = Environment(working_path=RAW_DIR)
    env.parse(CITY_MODEL.format(**fmt))
    executor = Dialects.DUCK_DB.default_executor(environment=env, conf=DuckDBConfig(enable_python_datasources=True))
    (sql,) = executor.generate_sql(
        "select tree_id, cluster_id, height_m where city = 'USBOS';"
    )
    # No approved tree reports in the fixture.
    sql = re.sub(
        r"uv_run\('[^']*tree_modification_info\.py'(?:,\s*args := '(?:[^']|'')*')?\)",
        "(SELECT * FROM (VALUES (CAST(NULL AS VARCHAR), CAST(NULL AS VARCHAR), CAST(NULL AS DOUBLE), "
        "CAST(NULL AS DOUBLE), CAST(NULL AS DOUBLE), CAST(NULL AS BOOLEAN))) "
        "AS t(tree_id, species, diameter_at_breast_height, latitude, longitude, missing) WHERE tree_id IS NOT NULL)",
        sql,
    )
    assert "uv_run" not in sql, sql
    for name, value in model_constants().items():
        sql = re.sub(rf":{name}\b", repr(value), sql)
    result = duckdb.connect().execute(sql)
    columns = [d[0] for d in result.description]
    rows = [dict(zip(columns, row)) for row in result.fetchall()]
    clusters = {row["tree_id"]: row["cluster_id"] for row in rows}
    survivors = {row["tree_id"]: row for row in rows if row["tree_id"] == row["cluster_id"]}
    # `merged_sources` does not plan against these inline fixtures (it does in
    # every city build), so absorption is read from the cluster ids instead.
    return {"clusters": clusters, **survivors}


def test_an_inventory_tree_under_a_crown_gets_its_height(published):
    row = published["m-under"]
    assert row["height_m"] == 15.5


def test_an_absorbed_prediction_lends_its_crown(published):
    """The inventory point misses; the prediction it absorbed is on the crown."""
    assert published["clusters"]["p-kerb"] == "m-kerb"
    row = published["m-kerb"]
    assert row["height_m"] == 11.0


def test_a_lone_prediction_and_a_lone_osm_node_get_theirs(published):
    assert published["p-alone"]["height_m"] == 7.5
    assert published["o-alone"]["height_m"] == 9.0


def test_no_crown_and_no_coordinates_stay_null(published):
    assert published["m-open"]["height_m"] is None
    assert published["m-nowhere"]["height_m"] is None


# --- the wiring ---------------------------------------------------------------


def height_cities() -> list[str]:
    return sorted(code for code, path in city_models().items() if "import ..tree_height;" in statements(path))


def test_importing_and_staging_agree():
    assert set(height_cities()) == set(CANOPY_LAYERS), (
        "a city importing tree_height and CANOPY_LAYERS in shared/canopy.py must "
        "agree, or the lookup is either never built or never read"
    )


@pytest.mark.parametrize("code", sorted(CANOPY_LAYERS))
def test_a_height_city_is_fully_wired(code: str):
    lower = code.lower()
    model = city_models()[code]
    text = statements(model)
    for partition in raw_partitions(code):
        assert "canopy_cell" not in stored(partition), f"{code}: {partition.name} maps canopy_cell as a column"

    assert f"root datasource {lower}_canopy_height (" in text
    assert height_staging_name(code) in text

    probe = f"{lower}_canopy_data_updated_through"
    assert f"data_updated_through: {probe}" in text
    assert probe in text.split(f"auto {lower}_published_data_updated_through <- greatest(", 1)[1].split(")", 1)[0]

    target = stored(published_target(code))
    assert "height_m" in target, f"{code}'s target does not publish height_m"
    # provenance stays in staging; see "What the published parquet carries" in tree_height.preql
    for column in ("height_source", "height_crown_id", "canopy_crown_id", "canopy_source"):
        assert column not in target, f"{code}'s target publishes {column}"

    staging = DATA_DIR / "canopy_staging" / f"{lower}_canopy_staging.preql"
    assert staging.exists(), f"{code} has no canopy staging model"
    staging_text = statements(staging)
    assert "file `./canopy_height_rows.py`" in staging_text
    assert f"where city = '{code}';" in staging_text
    assert height_staging_name(code) in staging_text
    job = jobs_by_key().get(f"canopy-{lower}")
    assert job, f"{code} has no canopy-{lower} job; nothing would ever rebuild its lookup"
    assert job["entrypoint"] == f"canopy_staging/{lower}_canopy_staging.preql"
