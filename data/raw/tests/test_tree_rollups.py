"""The dashboard summaries: tree counts at (city, species), and at (city,
species) plus one tree-level bucket (plant year, DBH class, dot-map cell,
kind of source, crown width class);
and two lookups beside them, each city's tree centroid and each species'
native range as lists.

`tree_rollups.preql` builds them from the published rollup; the browser reads
them through `tree_rollup_source.preql` and the planner answers a dashboard's
`count(tree_id)` from them instead of the city's tree file. A summary that disagrees with the trees is a wrong chart on every
dashboard, so the model's own SQL is run over a fixture rollup and compared
with the trees grouped by hand.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

RAW_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = RAW_DIR.parent

sys.path.insert(0, str(RAW_DIR))

MODEL = RAW_DIR / "tree_rollups.preql"
SOURCE = RAW_DIR / "tree_rollup_source.preql"


def statements(path: Path) -> str:
    return "\n".join(
        line.split("#", 1)[0] for line in path.read_text(encoding="utf-8").splitlines()
    )


# --- Wiring -----------------------------------------------------------------


def test_the_summary_consumes_the_published_rollup_by_name():
    """Ordered after publish-full by the `full_tree_info` root, never a city model."""
    model = statements(MODEL)
    assert "import full_tree_info_source;" in model
    assert "import full_tree_publish;" not in model
    assert "import tree_info;" not in model
    assert re.search(r"^datasource tree_rollup_species \(", model, re.M)


# Each summary and the bucket columns it adds to (city, species).
SUMMARIES = {
    "tree_rollup_species": [],
    "tree_rollup_year": ["plant_year"],
    "tree_rollup_dbh": ["dbh_class"],
    "tree_rollup_cell": ["map_cell_latitude", "map_cell_longitude"],
    "tree_rollup_coarse_cell": ["map_coarse_cell_latitude", "map_coarse_cell_longitude"],
    "tree_rollup_origin": ["source_kind"],
    "tree_rollup_crown": ["crown_class"],
}


# The lookups and their grain.
LOOKUPS = {"tree_rollup_city": "city", "species_native_range": "species"}


@pytest.mark.parametrize("name", [*SUMMARIES, *LOOKUPS])
def test_every_summary_has_a_browser_root(name: str):
    source, model = statements(SOURCE), statements(MODEL)
    assert re.search(rf"^datasource {name} \(", model, re.M)
    assert re.search(rf"^root datasource {name} \(", source, re.M)


def test_the_source_is_a_root_with_the_producers_name_and_derivation():
    """The browser's root carries the producer's name and its count.

    The name is the platform's edge from this job to anything that reads the
    summary; the derivation is what lets the planner recognise the stored
    column as `count(tree_id)` and re-sum it.
    """
    source, model = statements(SOURCE), statements(MODEL)
    assert re.search(r"^root datasource tree_rollup_species \(", source, re.M)
    derivation = re.compile(r"^auto rollup_tree_count <- count\(tree_id\);$", re.M)
    assert derivation.search(source) and derivation.search(model)
    assert "import tree_rollups;" not in source


def test_the_summary_has_a_job_on_the_core_cron():
    import tomllib

    jobs = tomllib.loads((DATA_DIR / "trilogy.toml").read_text(encoding="utf-8"))["cloud"]["job"]
    by_entrypoint = {job["entrypoint"]: job for job in jobs}
    job = by_entrypoint["raw/tree_rollups.preql"]
    assert job["operation"] == "refresh"
    assert job["schedule"] == by_entrypoint["raw/full_tree_publish.preql"]["schedule"]


def test_the_models_parse():
    from trilogy import Environment

    env = Environment(working_path=RAW_DIR)
    env.parse(MODEL.read_text(encoding="utf-8"))
    assert {"full_tree_info", "tree_rollup_species"} <= {d.name for d in env.datasources.values()}
    for name, columns in SUMMARIES.items():
        target = next(d for d in env.datasources.values() if d.name == name)
        assert target.grain.components == {"local.city", "local.species", *(f"local.{c}" for c in columns)}

    env = Environment(working_path=RAW_DIR)
    env.parse(SOURCE.read_text(encoding="utf-8"))
    assert set(SUMMARIES) | set(LOOKUPS) <= {d.name for d in env.datasources.values()}


# --- The rows ----------------------------------------------------------------

# (tree_id, city, species, plant_date, dbh, latitude, longitude, data_source, observed crown width)
FIXTURE_TREES = [
    ("sf-1", "USSFO", "Acer platanoides", "1998-03-04", 4.0, 37.7712, -122.4114, "SF_OPENDATA", None),
    ("sf-2", "USSFO", "Acer platanoides", "1998-07-01", 30.0, 37.7714, -122.4116, "OSM_USSFO", 13.0),  # observed crown wins over the estimate
    ("sf-3", "USSFO", "Newtaxon alba", None, 12.0, 37.7801, -122.4199, "COMMUNITY_USSFO", None),  # no enrichment row: still counted
    ("sf-4", "USSFO", "Unknown", "2015-01-01", None, None, None, "PREDICTED_USSFO", 4.0),  # sentinel, no position: still counted
    ("sf-5", "USSFO", "Acer platanoides", None, None, 37.7712, -122.4114, "SATELLITE_USSFO", None),  # no crown at all
    ("nyc-1", "USNYC", "Acer platanoides", "2010-05-05", 36.0, 40.7127, -74.0061, "NYC_OPENDATA", None),
    ("nyc-2", "USNYC", "Platanus x acerifolia", None, 5.9, 40.7127, -74.0061, "PREDICTED_USNYC", 2.9),  # no prediction row: still counted
]
# tree_id -> the allometric crown width the predictions table publishes.
# sf-4 and sf-5 have a row with a null width; nyc-2 has no row.
FIXTURE_PREDICTED_CROWN = {"sf-1": 4.9, "sf-2": 6.0, "sf-3": 8.0, "sf-4": None, "sf-5": None, "nyc-1": 12.0}


def crown_width(tree: tuple) -> float | None:
    return tree[8] if tree[8] is not None else FIXTURE_PREDICTED_CROWN.get(tree[0])


def bucket(name: str, tree: tuple) -> tuple:
    """The reference for each grain, written out instead of read from the model."""
    _, _, _, plant_date, dbh, lat, lon, data_source, _ = tree
    if name == "tree_rollup_year":
        return (int(plant_date[:4]) if plant_date else None,)
    if name == "tree_rollup_dbh":
        if dbh is None:
            return (None,)
        return (next((i for i, top in enumerate((6, 12, 24, 36), 1) if dbh < top), 5),)
    if name in ("tree_rollup_cell", "tree_rollup_coarse_cell"):
        digits = 3 if name == "tree_rollup_cell" else 2
        return (None, None) if lat is None else (round(lat, digits), round(lon, digits))
    if name == "tree_rollup_origin":
        prefixes = {"COMMUNITY_": "community", "SATELLITE_": "satellite", "OSM_": "osm", "PREDICTED_": "predicted"}
        return (next((kind for prefix, kind in prefixes.items() if data_source.startswith(prefix)), "municipal"),)
    if name == "tree_rollup_crown":
        width = crown_width(tree)
        if width is None:
            return (None,)
        return (next((i for i, top in enumerate((3, 5, 8, 12), 1) if width < top), 5),)
    return ()


@pytest.fixture(scope="module")
def rollup_parquet(tmp_path_factory) -> Path:
    import duckdb

    conn = duckdb.connect()
    conn.execute(
        """
        CREATE TABLE rollup (
            tree_id VARCHAR, city VARCHAR, data_source VARCHAR, species VARCHAR, cultivar VARCHAR,
            crown_width_m DOUBLE, tree_name VARCHAR, plant_date DATE, diameter_at_breast_height DOUBLE,
            latitude DOUBLE, longitude DOUBLE, submission_photo_url VARCHAR
        )
        """
    )
    conn.executemany(
        "INSERT INTO rollup VALUES (?, ?, ?, ?, NULL, ?, NULL, ?, ?, ?, ?, NULL)",
        [(t[0], t[1], t[7], t[2], t[8], t[3], t[4], t[5], t[6]) for t in FIXTURE_TREES],
    )
    path = tmp_path_factory.mktemp("rollups") / "rollup.parquet"
    conn.execute(f"COPY rollup TO '{path.as_posix()}' (FORMAT PARQUET)")
    return path


@pytest.fixture(scope="module")
def predictions_parquet(tmp_path_factory) -> Path:
    import duckdb

    conn = duckdb.connect()
    conn.execute(
        "CREATE TABLE p (tree_id VARCHAR, city VARCHAR, predicted_crown_width_m DOUBLE, predicted_height_m DOUBLE,"
        " accepted_height_m DOUBLE, prediction_inputs_published_through TIMESTAMPTZ)"
    )
    conn.executemany("INSERT INTO p VALUES (?, NULL, ?, NULL, NULL, NULL)", list(FIXTURE_PREDICTED_CROWN.items()))
    path = tmp_path_factory.mktemp("predictions") / "predictions.parquet"
    conn.execute(f"COPY p TO '{path.as_posix()}' (FORMAT PARQUET)")
    return path


# The measure columns a summary stores after its grain, where not just the count.
MEASURES = {"tree_rollup_crown": ["rollup_tree_count", "rollup_canopy_area_m2"]}


@pytest.fixture(scope="module", params=list(SUMMARIES))
def summary(request, rollup_parquet, predictions_parquet) -> tuple[str, list[tuple]]:
    """The model's own SQL for one target, run over a fixture rollup."""
    import duckdb
    from trilogy import Dialects, Environment

    name = request.param
    env = Environment(working_path=RAW_DIR)
    env.parse(MODEL.read_text(encoding="utf-8"))
    # Ask for the derivation, not the published summaries.
    for key in [k for k, d in env.datasources.items() if d.name in SUMMARIES]:
        del env.datasources[key]
    executor = Dialects.DUCK_DB.default_executor(environment=env)
    columns = ", ".join(["city", "species", *SUMMARIES[name], *MEASURES.get(name, ["rollup_tree_count"])])
    (sql,) = executor.generate_sql(f"select {columns};")
    sql, n = re.subn(r"read_parquet\('[^']*full_tree_info_v[^']*'\)", f"read_parquet('{rollup_parquet.as_posix()}')", sql)
    assert n == 1, f"expected one scan of the rollup, found {n}"
    # Only the crown summary reads a second table: the estimated widths, one
    # multi-file scan over every city's predictions file.
    sql, n = re.subn(
        r"read_parquet\(\['[^\]]*_tree_predictions_v[^\]]*\]\)", f"read_parquet('{predictions_parquet.as_posix()}')", sql
    )
    assert n == (1 if name == "tree_rollup_crown" else 0)
    assert "read_parquet('https" not in sql
    return name, duckdb.connect().execute(sql).fetchall()


def grain_width(name: str) -> int:
    return 2 + len(SUMMARIES[name])


def test_the_summary_is_the_trees_grouped(summary):
    name, rows = summary
    expected: dict[tuple, int] = {}
    for tree in FIXTURE_TREES:
        key = (tree[1], tree[2], *bucket(name, tree))
        expected[key] = expected.get(key, 0) + 1
    width = grain_width(name)
    got = {tuple(row[:width]): row[width] for row in rows}
    assert got == expected


def test_one_row_per_grain(summary):
    name, rows = summary
    keys = [tuple(row[: grain_width(name)]) for row in rows]
    assert len(keys) == len(set(keys))


def test_the_crown_summary_sums_each_crown_as_a_circle(summary):
    """Observed width first, the estimate otherwise; a tree with neither adds no area."""
    import math

    name, rows = summary
    if name != "tree_rollup_crown":
        pytest.skip("only the crown summary stores an area")
    expected: dict[tuple, float] = {}
    for tree in FIXTURE_TREES:
        width = crown_width(tree)
        if width is not None:
            key = (tree[1], tree[2], *bucket(name, tree))
            expected[key] = expected.get(key, 0.0) + math.pi * (width / 2) ** 2
    got = {tuple(row[:3]): row[4] for row in rows if row[4] is not None}
    assert got == {key: pytest.approx(area) for key, area in expected.items()}


# --- The lookups ---------------------------------------------------------------

# (species, native_ecoregions). "Platanus x acerifolia" has trees but no
# enrichment row, so no range row either: its bucket is the fall-through.
FIXTURE_ENRICHMENT = [
    ("Acer platanoides", [1, 2]),
    ("Newtaxon alba", None),
    ("Unknown", None),
]
# (ecoregion_id, biome, realm); 3 is native to no species, as most are.
FIXTURE_ECOREGIONS = [
    (1, "Temperate Broadleaf", "palearctic"),
    (2, "Temperate Broadleaf", "nearctic"),
    (3, "Tundra", "nearctic"),
]


@pytest.fixture(scope="module")
def lookup_inputs(tmp_path_factory, rollup_parquet) -> dict[str, Path]:
    import duckdb

    folder = tmp_path_factory.mktemp("lookups")
    conn = duckdb.connect()
    conn.execute("CREATE TABLE e (species VARCHAR, genus VARCHAR, tree_form VARCHAR, common_names VARCHAR[], native_ecoregions INTEGER[])")
    conn.executemany("INSERT INTO e VALUES (?, NULL, NULL, NULL, ?)", FIXTURE_ENRICHMENT)
    conn.execute("CREATE TABLE r (ecoregion_id INTEGER, biome VARCHAR, realm VARCHAR, data_updated_through TIMESTAMPTZ)")
    conn.executemany("INSERT INTO r VALUES (?, ?, ?, NULL)", FIXTURE_ECOREGIONS)
    paths = {"full_tree_info": rollup_parquet, "tree_enrichment": folder / "e.parquet", "ecoregion_info": folder / "r.parquet"}
    conn.execute(f"COPY e TO '{paths['tree_enrichment'].as_posix()}' (FORMAT PARQUET)")
    conn.execute(f"COPY r TO '{paths['ecoregion_info'].as_posix()}' (FORMAT PARQUET)")
    return paths


def lookup_rows(name: str, columns: list[str], inputs: dict[str, Path]) -> list[tuple]:
    """The model's own SQL for one lookup, run over the fixture inputs."""
    import duckdb
    from trilogy import Dialects, Environment

    env = Environment(working_path=RAW_DIR)
    env.parse(MODEL.read_text(encoding="utf-8"))
    for key in [k for k, d in env.datasources.items() if d.name == name]:
        del env.datasources[key]
    executor = Dialects.DUCK_DB.default_executor(environment=env)
    (sql,) = executor.generate_sql(f"select {', '.join(columns)};")
    for source, path in inputs.items():
        sql = re.sub(rf"read_parquet\('[^']*{source}_v[^']*'\)", f"read_parquet('{path.as_posix()}')", sql)
    assert "read_parquet('https" not in sql
    return duckdb.connect().execute(sql).fetchall()


def test_the_city_centroid_is_the_mean_tree_position(lookup_inputs):
    rows = lookup_rows("tree_rollup_city", ["city", "city_latitude", "city_longitude"], lookup_inputs)
    expected = {}
    for city in {tree[1] for tree in FIXTURE_TREES}:
        placed = [tree for tree in FIXTURE_TREES if tree[1] == city and tree[5] is not None]
        expected[city] = (
            pytest.approx(sum(tree[5] for tree in placed) / len(placed)),
            pytest.approx(sum(tree[6] for tree in placed) / len(placed)),
        )
    assert {row[0]: (row[1], row[2]) for row in rows} == expected


def test_the_native_range_is_the_ecoregions_and_their_biomes_and_realms(lookup_inputs):
    """One row per enriched species, and none for an ecoregion no species is native to.

    The planner FULL JOINs the merged ecoregion dimension onto the unnested
    lists, and the ecoregions nobody references come back as one null-species
    row (pytrilogy #835).
    """
    rows = lookup_rows("species_native_range", ["species", "native_ecoregions", "native_biomes", "native_realms"], lookup_inputs)
    assert {row[0]: tuple(row[1:]) for row in rows} == {
        "Acer platanoides": ([1, 2], ["Temperate Broadleaf"], ["nearctic", "palearctic"]),
        "Newtaxon alba": (None, None, None),
        "Unknown": (None, None, None),
    }
