"""The dashboard summaries: tree counts at (city, species), and at (city,
species) plus one tree-level bucket (plant year, DBH class, dot-map cell).

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
}


@pytest.mark.parametrize("name", SUMMARIES)
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
    assert set(SUMMARIES) <= {d.name for d in env.datasources.values()}


# --- The rows ----------------------------------------------------------------

# (tree_id, city, species, plant_date, dbh, latitude, longitude)
FIXTURE_TREES = [
    ("sf-1", "USSFO", "Acer platanoides", "1998-03-04", 4.0, 37.7712, -122.4114),
    ("sf-2", "USSFO", "Acer platanoides", "1998-07-01", 30.0, 37.7714, -122.4116),
    ("sf-3", "USSFO", "Newtaxon alba", None, 12.0, 37.7801, -122.4199),  # no enrichment row: still counted
    ("sf-4", "USSFO", "Unknown", "2015-01-01", None, None, None),        # sentinel, no position: still counted
    ("nyc-1", "USNYC", "Acer platanoides", "2010-05-05", 36.0, 40.7127, -74.0061),
    ("nyc-2", "USNYC", "Platanus x acerifolia", None, 5.9, 40.7127, -74.0061),
]


def bucket(name: str, tree: tuple) -> tuple:
    """The reference for each grain, written out instead of read from the model."""
    _, _, _, plant_date, dbh, lat, lon = tree
    if name == "tree_rollup_year":
        return (int(plant_date[:4]) if plant_date else None,)
    if name == "tree_rollup_dbh":
        if dbh is None:
            return (None,)
        return (next((i for i, top in enumerate((6, 12, 24, 36), 1) if dbh < top), 5),)
    if name in ("tree_rollup_cell", "tree_rollup_coarse_cell"):
        digits = 3 if name == "tree_rollup_cell" else 2
        return (None, None) if lat is None else (round(lat, digits), round(lon, digits))
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
        "INSERT INTO rollup VALUES (?, ?, 'FIXTURE', ?, NULL, NULL, NULL, ?, ?, ?, ?, NULL)",
        FIXTURE_TREES,
    )
    path = tmp_path_factory.mktemp("rollups") / "rollup.parquet"
    conn.execute(f"COPY rollup TO '{path.as_posix()}' (FORMAT PARQUET)")
    return path


@pytest.fixture(scope="module", params=list(SUMMARIES))
def summary(request, rollup_parquet) -> tuple[str, list[tuple]]:
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
    columns = ", ".join(["city", "species", *SUMMARIES[name], "rollup_tree_count"])
    (sql,) = executor.generate_sql(f"select {columns};")
    sql, n = re.subn(r"read_parquet\('[^']*full_tree_info_v[^']*'\)", f"read_parquet('{rollup_parquet.as_posix()}')", sql)
    assert n == 1, f"expected one scan of the rollup, found {n}"
    assert "read_parquet('https" not in sql
    return name, duckdb.connect().execute(sql).fetchall()


def test_the_summary_is_the_trees_grouped(summary):
    name, rows = summary
    expected: dict[tuple, int] = {}
    for tree in FIXTURE_TREES:
        key = (tree[1], tree[2], *bucket(name, tree))
        expected[key] = expected.get(key, 0) + 1
    got = {tuple(row[:-1]): row[-1] for row in rows}
    assert got == expected


def test_one_row_per_grain(summary):
    _, rows = summary
    keys = [tuple(row[:-1]) for row in rows]
    assert len(keys) == len(set(keys))
