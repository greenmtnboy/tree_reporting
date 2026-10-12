"""What a city build publishes once approved tree reports are applied.

The SF model's own build SQL -- planned by pytrilogy exactly as `trilogy
refresh` plans a stale target, the way test_satellite_reconciliation.py does
-- run against fixture rows for its python sources, with
tree_modification_info.py's output repointed at a fixture of resolved reports.
The reports land after the cluster merge, on the tree the reporter saw:

* a corrected species or DBH replaces the merged value;
* a corrected position moves the tree;
* a tree reported gone is not published, and neither is anything its cluster
  absorbed;
* a report for a tree this city does not have adds nothing (the partial merge
  is a LEFT join, not a FULL one).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

RAW_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAW_DIR))

MODEL = RAW_DIR / "ussfo" / "sf_tree_info.preql"

P1 = (37.7700, -122.4200)
P2 = (37.7750, -122.4250)
P3 = (37.7800, -122.4300)
P4 = (37.7850, -122.4350)
P5 = (37.7900, -122.4400)
NUDGE = 0.000012  # ~1.3 m of latitude
MOVED = (P2[0] + 0.00003, P2[1])  # ~3 m north of where the city put sf-2

# (tree_id, data_source, species, dbh, point)
MUNICIPAL = [
    ("sf-1", "SF_OPENDATA", "Quercus agrifolia", 20.0, P1),
    ("sf-2", "SF_OPENDATA", "Unknown", None, P2),
    ("sf-3", "SF_OPENDATA", "Acer rubrum", 9.0, P3),
    ("sf-4", "SF_OPENDATA", "Pinus radiata", 12.0, P4),
]
COMMUNITY = [("community-x", "COMMUNITY_USSFO", "Acer rubrum", None, P5)]
OSM = [("osm-1", "OSM_USSFO", None, None, (P3[0] + NUDGE, P3[1]))]  # absorbed by sf-3
# (tree_id, species, dbh, latitude, longitude, missing), as tree_modification_info.py emits them
REPORTS = [
    ("sf-1", "Quercus lobata", 22.5, None, None, False),
    ("sf-2", None, None, MOVED[0], MOVED[1], False),
    ("sf-3", None, None, None, None, True),
    ("community-x", "Acer saccharum", None, None, None, False),
    ("nyc-999", "Ulmus americana", 4.0, 40.7, -74.0, False),
]


def write_rows(conn, path: Path, rows) -> None:
    conn.execute("DROP TABLE IF EXISTS src")
    conn.execute(
        """CREATE TABLE src (tree_id VARCHAR, city VARCHAR, data_source VARCHAR, species VARCHAR,
        tree_name VARCHAR, plant_date DATE, diameter_at_breast_height DOUBLE, latitude DOUBLE,
        longitude DOUBLE, submission_photo_url VARCHAR, cultivar VARCHAR, crown_width_m DOUBLE)"""
    )
    if rows:
        conn.executemany(
            "INSERT INTO src VALUES (?, 'USSFO', ?, ?, NULL, NULL, ?, ?, ?, NULL, NULL, NULL)",
            [(tree_id, source, species, dbh, *point) for tree_id, source, species, dbh, point in rows],
        )
    conn.execute(f"COPY src TO '{path.as_posix()}' (FORMAT PARQUET)")


def write_reports(conn, path: Path, rows) -> None:
    conn.execute("DROP TABLE IF EXISTS mods")
    conn.execute(
        "CREATE TABLE mods (tree_id VARCHAR, species VARCHAR, diameter_at_breast_height DOUBLE, "
        "latitude DOUBLE, longitude DOUBLE, missing BOOLEAN)"
    )
    conn.executemany("INSERT INTO mods VALUES (?, ?, ?, ?, ?, ?)", rows)
    conn.execute(f"COPY mods TO '{path.as_posix()}' (FORMAT PARQUET)")


@pytest.fixture(scope="module")
def published(tmp_path_factory) -> dict[str, dict]:
    import duckdb
    from trilogy import Dialects, Environment
    from trilogy.dialect.config import DuckDBConfig

    tmp = tmp_path_factory.mktemp("modifications")
    conn = duckdb.connect()
    fixtures = {
        "sf_tree_info.py": tmp / "municipal.parquet",
        "community_tree_info.py": tmp / "community.parquet",
        "satellite_tree_info.py": tmp / "satellite.parquet",
        "ussfo_osm_staging.parquet": tmp / "osm.parquet",
        "tree_modification_info.py": tmp / "reports.parquet",
    }
    write_rows(conn, fixtures["sf_tree_info.py"], MUNICIPAL)
    write_rows(conn, fixtures["community_tree_info.py"], COMMUNITY)
    write_rows(conn, fixtures["satellite_tree_info.py"], [])
    write_rows(conn, fixtures["ussfo_osm_staging.parquet"], OSM)
    write_reports(conn, fixtures["tree_modification_info.py"], REPORTS)

    env = Environment(working_path=MODEL.parent)
    env.parse(MODEL.read_text(encoding="utf-8"))
    executor = Dialects.DUCK_DB.default_executor(
        environment=env, conf=DuckDBConfig(enable_python_datasources=True)
    )
    sql = executor.update_datasource(env.datasources["sf_tree_info"], dry_run=True)
    sql = re.sub(r'^.*?INSERT INTO "[^"]*"\s*', "", sql, count=1, flags=re.S)
    assert sql.lstrip().upper().startswith("WITH"), sql[:200]
    probe = (
        "(SELECT TIMESTAMPTZ '2026-01-01 00:00:00+00' AS data_updated_through, "
        "TIMESTAMPTZ '2026-01-01 00:00:00+00' AS ussfo_community_data_updated_through, "
        "TIMESTAMPTZ '2026-01-01 00:00:00+00' AS ussfo_satellite_data_updated_through)"
    )

    def repoint(match: re.Match) -> str:
        script = Path(match.group(1)).name
        if script in fixtures:
            return f"read_parquet('{fixtures[script].as_posix()}')"
        assert script.endswith(("_update_time.py", "_probe.py")), script
        return probe

    sql = re.sub(r"uv_run\('([^']+)'(?:,\s*args := '(?:[^']|'')*')?\)", repoint, sql)
    sql = re.sub(
        r"read_parquet\('[^']*ussfo_osm_staging\.parquet[^']*'\)",
        f"read_parquet('{fixtures['ussfo_osm_staging.parquet'].as_posix()}')",
        sql,
    )
    assert "uv_run" not in sql and "storage.googleapis.com" not in sql, sql
    result = conn.execute(sql)
    columns = [d[0] for d in result.description]
    return {row["tree_id"]: row for row in (dict(zip(columns, r)) for r in result.fetchall())}


def test_only_the_trees_still_standing_publish(published):
    assert set(published) == {"sf-1", "sf-2", "sf-4", "community-x"}, (
        "sf-3 was reported gone and osm-1 was its duplicate; nyc-999 is not an SF tree"
    )


def test_a_corrected_species_and_dbh_replace_the_merged_values(published):
    row = published["sf-1"]
    assert row["species"] == "Quercus lobata"
    assert row["diameter_at_breast_height"] == 22.5
    assert (row["latitude"], row["longitude"]) == P1


def test_a_corrected_position_moves_the_tree(published):
    row = published["sf-2"]
    assert (row["latitude"], row["longitude"]) == MOVED
    assert row["species"] == "Unknown", "a report that leaves the species alone keeps the source's"


def test_a_report_corrects_a_community_tree_too(published):
    assert published["community-x"]["species"] == "Acer saccharum"


def test_an_unreported_tree_is_untouched(published):
    row = published["sf-4"]
    assert (row["species"], row["diameter_at_breast_height"], row["latitude"]) == ("Pinus radiata", 12.0, P4[0])


def test_the_published_schema_is_unchanged(published):
    """A tree reported gone is dropped through cluster_id, not a new column:
    a target's `where` runs on every read, so a new column in it would fail
    every read of a parquet built before it existed."""
    assert "reported_missing" not in next(iter(published.values()))
    assert all(row["tree_id"] == row["cluster_id"] for row in published.values())
