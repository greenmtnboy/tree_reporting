"""The dashboard summary: tree counts at (city, species).

`tree_rollups.preql` builds `tree_rollup_species` from the published rollup;
the browser reads it through `tree_rollup_source.preql` and the planner
answers a dashboard's `count(tree_id)` from it instead of the city's tree
file. A summary that disagrees with the trees is a wrong chart on every
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
    target = next(d for d in env.datasources.values() if d.name == "tree_rollup_species")
    assert target.grain.components == {"local.city", "local.species"}

    env = Environment(working_path=RAW_DIR)
    env.parse(SOURCE.read_text(encoding="utf-8"))
    assert "tree_rollup_species" in {d.name for d in env.datasources.values()}


# --- The rows ----------------------------------------------------------------

FIXTURE_TREES = [
    ("sf-1", "USSFO", "Acer platanoides"),
    ("sf-2", "USSFO", "Acer platanoides"),
    ("sf-3", "USSFO", "Newtaxon alba"),   # no enrichment row: still counted
    ("sf-4", "USSFO", "Unknown"),         # sentinel: still counted
    ("nyc-1", "USNYC", "Acer platanoides"),
    ("nyc-2", "USNYC", "Platanus x acerifolia"),
]


@pytest.fixture(scope="module")
def summary(tmp_path_factory) -> list[tuple]:
    """The model's own SQL for the target, run over a fixture rollup."""
    import duckdb
    from trilogy import Dialects, Environment

    tmp = tmp_path_factory.mktemp("rollups")
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
        "INSERT INTO rollup VALUES (?, ?, 'FIXTURE', ?, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL)",
        FIXTURE_TREES,
    )
    path = tmp / "rollup.parquet"
    conn.execute(f"COPY rollup TO '{path.as_posix()}' (FORMAT PARQUET)")

    env = Environment(working_path=RAW_DIR)
    env.parse(MODEL.read_text(encoding="utf-8"))
    # Ask for the derivation, not the published summary.
    for key in [k for k, d in env.datasources.items() if d.name == "tree_rollup_species"]:
        del env.datasources[key]
    executor = Dialects.DUCK_DB.default_executor(environment=env)
    (sql,) = executor.generate_sql("select city, species, rollup_tree_count;")
    sql, n = re.subn(r"read_parquet\('[^']*full_tree_info_v[^']*'\)", f"read_parquet('{path.as_posix()}')", sql)
    assert n == 1, f"expected one scan of the rollup, found {n}"
    assert "read_parquet('https" not in sql
    return conn.execute(sql).fetchall()


def test_the_summary_is_the_trees_grouped(summary):
    expected: dict[tuple[str, str], int] = {}
    for _, city, species in FIXTURE_TREES:
        expected[(city, species)] = expected.get((city, species), 0) + 1
    assert {(city, species): n for city, species, n in summary} == expected


def test_one_row_per_city_and_species(summary):
    keys = [(city, species) for city, species, _ in summary]
    assert len(keys) == len(set(keys))
