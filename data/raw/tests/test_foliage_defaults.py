"""Whether a genus drops its leaves where it stands (foliage_defaults.preql).

The map draws a tree recorded only to its genus -- or of a species whose leaf
habit the enrichment left unknown -- bare in winter when most identified trees
of that genus around it are deciduous. A wrong share is a Florida live oak
drawn leafless, so the model's own SQL is run over fixture trees and checked
against shares worked out by hand at each fallback level.
"""

from __future__ import annotations

import math
import re
import sys
import tomllib
from pathlib import Path

import pytest

RAW_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = RAW_DIR.parent
REPO_DIR = DATA_DIR.parent

sys.path.insert(0, str(RAW_DIR))

MODEL = RAW_DIR / "foliage_defaults.preql"


def statements(path: Path) -> str:
    return "\n".join(
        line.split("#", 1)[0] for line in path.read_text(encoding="utf-8").splitlines()
    )


def cell(lat: float, lng: float) -> int:
    """The 1-degree cell, as the model and the browser (foliageCellSql) both compute it."""
    return (math.floor(lat) + 90) * 360 + math.floor(lng) + 180


# --- Wiring -----------------------------------------------------------------


def test_the_model_reads_published_parquets_by_name():
    """Ordered after publish-full and refresh-enrichment by their root names, never their models."""
    model = statements(MODEL)
    assert "import full_tree_info_source;" in model
    assert "import tree_enrichment_source;" in model
    assert "import tree_enrichment;" not in model
    assert "import full_tree_publish;" not in model
    assert re.search(r"^datasource foliage_defaults \(", model, re.M)


def test_the_model_has_a_job_on_the_core_cron():
    jobs = tomllib.loads((DATA_DIR / "trilogy.toml").read_text(encoding="utf-8"))["cloud"]["job"]
    by_entrypoint = {job["entrypoint"]: job for job in jobs}
    job = by_entrypoint["raw/foliage_defaults.preql"]
    assert job["operation"] == "refresh"
    assert job["schedule"] == by_entrypoint["raw/full_tree_publish.preql"]["schedule"]


def test_the_model_parses_at_its_grain():
    from trilogy import Environment

    env = Environment(working_path=RAW_DIR)
    env.parse(MODEL.read_text(encoding="utf-8"))
    target = next(d for d in env.datasources.values() if d.name == "foliage_defaults")
    assert target.grain.components == {"local.foliage_cell", "local.genus"}


def test_the_browser_computes_the_same_cell():
    """The worker joins on the cell it computes in SQL; both pin Boston's cell."""
    assert cell(42.36, -71.06) == 47628
    source = (REPO_DIR / "src/src/lib/foliageSeason.ts").read_text(encoding="utf-8")
    assert "+ 90) * 360" in source and "+ 180" in source
    test = (REPO_DIR / "src/src/lib/foliageSeason.test.ts").read_text(encoding="utf-8")
    assert "47628" in test


# --- The rows ----------------------------------------------------------------

BOSTON = (42.36, -71.06)
NAPLES = (26.15, -81.79)
# Another 1-degree cell in Naples's 5-degree region, with no identified oak of its own.
NEAR_NAPLES = (27.5, -80.5)
LEIPZIG = (51.34, 12.37)

# (species, genus, is_evergreen)
ENRICHMENT = [
    ("Quercus", "Quercus", None),  # the genus alone: open
    ("Quercus rubra", "Quercus", False),
    ("Quercus virginiana", "Quercus", True),
    ("Quercus robur", "Quercus", False),
    ("Quercus mystica", "Quercus", None),  # a binomial of unknown habit: open, never counted
    ("Rarus alba", "Rarus", False),
    ("Rarus", "Rarus", None),
]


def fixture_trees() -> list[tuple]:
    trees: list[tuple] = []

    def add(species: str, where: tuple[float, float] | None, n: int = 1) -> None:
        for _ in range(n):
            lat, lng = where if where else (None, None)
            trees.append((f"t-{len(trees)}", species, lat, lng))

    add("Quercus rubra", BOSTON, 25)
    add("Quercus", BOSTON, 2)
    add("Quercus mystica", BOSTON)
    add("Quercus virginiana", NAPLES, 25)
    add("Quercus", NAPLES)
    add("Quercus", NEAR_NAPLES)
    add("Quercus robur", LEIPZIG, 3)
    add("Quercus", LEIPZIG)
    add("Rarus alba", BOSTON, 2)
    add("Rarus", BOSTON)
    add("Newtaxon alba", BOSTON)  # no enrichment row: no genus, no row
    add("Quercus", None)  # no position: no cell, no row
    return trees


@pytest.fixture(scope="module")
def rows(tmp_path_factory) -> dict[tuple[int, str], tuple]:
    """The model's own SQL for the published table, run over fixture parquets."""
    import duckdb
    from trilogy import Dialects, Environment

    tmp = tmp_path_factory.mktemp("foliage")
    conn = duckdb.connect()
    conn.execute(
        "CREATE TABLE t (tree_id VARCHAR, city VARCHAR, data_source VARCHAR, species VARCHAR, cultivar VARCHAR,"
        " crown_width_m DOUBLE, tree_name VARCHAR, significance VARCHAR, significance_source VARCHAR,"
        " plant_date DATE, diameter_at_breast_height DOUBLE, latitude DOUBLE, longitude DOUBLE,"
        " submission_photo_url VARCHAR)"
    )
    conn.executemany(
        "INSERT INTO t VALUES (?, 'X', 'X', ?, NULL, NULL, NULL, NULL, NULL, NULL, NULL, ?, ?, NULL)",
        fixture_trees(),
    )
    trees = tmp / "trees.parquet"
    conn.execute(f"COPY t TO '{trees.as_posix()}' (FORMAT PARQUET)")
    conn.execute(
        "CREATE TABLE e (species VARCHAR, genus VARCHAR, tree_form VARCHAR, common_names VARCHAR[],"
        " native_ecoregions INTEGER[], is_evergreen BOOLEAN)"
    )
    conn.executemany("INSERT INTO e VALUES (?, ?, 'default', [], [], ?)", ENRICHMENT)
    enrichment = tmp / "enrichment.parquet"
    conn.execute(f"COPY e TO '{enrichment.as_posix()}' (FORMAT PARQUET)")

    env = Environment(working_path=RAW_DIR)
    env.parse(MODEL.read_text(encoding="utf-8"))
    # Ask for the derivation, not the published table.
    for key in [k for k, d in env.datasources.items() if d.name == "foliage_defaults"]:
        del env.datasources[key]
    executor = Dialects.DUCK_DB.default_executor(environment=env)
    (sql,) = executor.generate_sql(
        "select foliage_cell, genus, foliage_deciduous_share, foliage_default_level, foliage_cell_open"
        " where foliage_cell is not null and genus is not null;"
    )
    sql, n = re.subn(r"read_parquet\('[^']*full_tree_info_v[^']*'\)", f"read_parquet('{trees.as_posix()}')", sql)
    assert n == 1, f"expected one scan of the rollup, found {n}"
    sql, n = re.subn(r"read_parquet\('[^']*tree_enrichment_v[^']*'\)", f"read_parquet('{enrichment.as_posix()}')", sql)
    assert n == 1, f"expected one scan of the enrichment table, found {n}"
    assert "read_parquet('https" not in sql
    out = duckdb.connect().execute(sql).fetchall()
    keys = [(row[0], row[1]) for row in out]
    assert len(keys) == len(set(keys)), "one row per (cell, genus)"
    return {(row[0], row[1]): row[2:] for row in out}


def test_a_cell_with_enough_identified_trees_answers_for_itself(rows):
    """Boston's generic oak is bare in winter; Naples's is not."""
    assert rows[(cell(*BOSTON), "Quercus")] == (pytest.approx(1.0), "cell", 3)
    assert rows[(cell(*NAPLES), "Quercus")] == (pytest.approx(0.0), "cell", 1)


def test_a_thin_cell_borrows_its_region(rows):
    share, level, open_trees = rows[(cell(*NEAR_NAPLES), "Quercus")]
    assert (share, level, open_trees) == (pytest.approx(0.0), "region", 1)


def test_a_thin_region_falls_back_to_the_genus(rows):
    # 25 + 3 deciduous of 53 identified oaks; the unknown-habit binomial is not counted.
    share, level, _ = rows[(cell(*LEIPZIG), "Quercus")]
    assert level == "genus"
    assert share == pytest.approx(28 / 53)


def test_too_few_identified_trees_anywhere_gives_no_share(rows):
    share, level, open_trees = rows[(cell(*BOSTON), "Rarus")]
    assert share is None and level is None
    assert open_trees == 1


def test_only_enriched_positioned_trees_have_rows(rows):
    assert {genus for _, genus in rows} == {"Quercus", "Rarus"}
    assert len(rows) == 5
