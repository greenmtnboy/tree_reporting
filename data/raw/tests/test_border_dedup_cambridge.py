"""Cambridge's border dedup between the city's inventory and MIT's.

Cambridge's own build SQL, planned by pytrilogy and run against fixture rows,
the way test_border_dedup_melbourne.py runs Melbourne's.  The rule
(uscam/cambridge_tree_info.preql): an MIT row is dropped when a city row of
the same genus shares one of four staggered 3 m cells with it; the city's row
is the one kept.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

RAW_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAW_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

MODEL = RAW_DIR / "uscam" / "cambridge_tree_info.preql"
M_PER_DEG_LAT = 111_320.0

# Points well apart on and around MIT's campus, inside Cambridge.
P1 = (42.3590, -71.0940)
P2 = (42.3600, -71.0920)
P3 = (42.3610, -71.0900)
P4 = (42.3585, -71.0960)


def north(point, metres):
    return (point[0] + metres / M_PER_DEG_LAT, point[1])


# (tree_id, data_source, species, point)
SOURCES = {
    "cambridge_tree_info.py": [
        ("cam-lind", "CAMBRIDGE", "Tilia cordata", P1),
        ("cam-oak", "CAMBRIDGE", "Quercus palustris", P2),
        ("cam-plane", "CAMBRIDGE", "Platanus x acerifolia", P3),
    ],
    "mit_tree_info.py": [
        # The same linden, 1.2 m off: the city's row is kept.
        ("mit-1", "MIT", "Tilia americana", north(P1, 1.2)),
        # A honeylocust 1.2 m from the city's oak: a different tree.
        ("mit-2", "MIT", "Gleditsia triacanthos", north(P2, 1.2)),
        # Same genus, 8 m along: the next tree.
        ("mit-3", "MIT", "Platanus x acerifolia", north(P3, 8.0)),
        # Two MIT elms 1.2 m apart and no city tree: kept, both.
        ("mit-4", "MIT", "Ulmus americana", P4),
        ("mit-5", "MIT", "Ulmus americana", north(P4, 1.2)),
    ],
}


def write_rows(conn, path: Path, rows) -> None:
    conn.execute("DROP TABLE IF EXISTS src")
    conn.execute(
        """
        CREATE TABLE src (
            tree_id VARCHAR, city VARCHAR, data_source VARCHAR, species VARCHAR,
            tree_name VARCHAR, plant_date DATE, diameter_at_breast_height DOUBLE,
            latitude DOUBLE, longitude DOUBLE, submission_photo_url VARCHAR, cultivar VARCHAR,
            crown_width_m DOUBLE, significance VARCHAR, significance_source VARCHAR,
            linked_tree_id VARCHAR
        )
        """
    )
    if rows:
        conn.executemany(
            "INSERT INTO src VALUES (?, 'USCAM', ?, ?, NULL, NULL, 10.0, ?, ?, NULL, NULL, NULL, NULL, NULL, NULL)",
            [(tid, source, species, *point) for tid, source, species, point in rows],
        )
    conn.execute(f"COPY src TO '{path.as_posix()}' (FORMAT PARQUET)")


@pytest.fixture(scope="module")
def published(tmp_path_factory) -> dict[str, dict]:
    import duckdb
    from trilogy import Dialects, Environment
    from trilogy.dialect.config import DuckDBConfig

    from test_satellite_reconciliation import write_modifications

    tmp = tmp_path_factory.mktemp("border_dedup_cambridge")
    conn = duckdb.connect()
    conn.execute("INSTALL spatial; LOAD spatial;")
    scripts = [
        "cambridge_tree_info.py", "mit_tree_info.py", "community_tree_info.py",
        "satellite_tree_info.py", "predicted_tree_info.py", "uscam_osm_staging.parquet",
    ]
    fixtures = {}
    for script in scripts:
        fixtures[script] = tmp / f"{script}.parquet"
        write_rows(conn, fixtures[script], SOURCES.get(script, []))
    fixtures["tree_modification_info.py"] = tmp / "modifications.parquet"
    write_modifications(conn, fixtures["tree_modification_info.py"], [])

    env = Environment(working_path=MODEL.parent)
    env.parse(MODEL.read_text(encoding="utf-8"))
    executor = Dialects.DUCK_DB.default_executor(
        environment=env, conf=DuckDBConfig(enable_python_datasources=True)
    )
    sql = executor.update_datasource(env.datasources["cambridge_tree_info"], dry_run=True)
    assert sql is not None
    sql = re.sub(r'^.*?INSERT INTO "[^"]*"\s*', "", sql, count=1, flags=re.S)
    assert sql.lstrip().upper().startswith("WITH"), sql[:200]

    stamp = "TIMESTAMPTZ '2026-01-01 00:00:00+00'"
    probe = "(SELECT " + ", ".join(
        f"{stamp} AS {col}"
        for col in (
            "data_updated_through",
            "uscam_community_data_updated_through",
            "uscam_satellite_data_updated_through",
            "uscam_predicted_data_updated_through",
        )
    ) + ")"

    def repoint(match: re.Match) -> str:
        script = Path(match.group(1)).name
        if script in fixtures:
            return f"read_parquet('{fixtures[script].as_posix()}')"
        assert script.endswith(("_update_time.py", "_probe.py")), script
        return probe

    sql = re.sub(r"uv_run\('([^']+)'(?:,\s*args := '(?:[^']|'')*')?\)", repoint, sql)
    sql = re.sub(
        r"read_parquet\('[^']*uscam_osm_staging\.parquet[^']*'\)",
        f"read_parquet('{fixtures['uscam_osm_staging.parquet'].as_posix()}')",
        sql,
    )
    # The LIDAR height lookup: empty here (test_tree_height.py covers it).
    sql = re.sub(
        r"read_parquet\('[^']*usbos_canopy_height\.parquet[^']*'\)",
        "(SELECT CAST(NULL AS BIGINT) AS canopy_cell, CAST(NULL AS VARCHAR) AS canopy_crown_id, "
        "CAST(NULL AS DOUBLE) AS canopy_height_m, CAST(NULL AS DOUBLE) AS canopy_crown_radius_m, "
        "CAST(NULL AS VARCHAR) AS canopy_source WHERE false)",
        sql,
    )
    height_model = (RAW_DIR / "tree_height.preql").read_text(encoding="utf-8")
    for name, value in re.findall(r"^const (\w+) <- ([-\d.]+);", height_model, re.M):
        sql = re.sub(rf":{name}\b", value, sql)
    assert "uv_run" not in sql and "storage.googleapis.com" not in sql, sql
    cursor = conn.execute(sql)
    columns = [d[0] for d in cursor.description]
    return {row["tree_id"]: row for row in (dict(zip(columns, r)) for r in cursor.fetchall())}


def test_the_same_tree_in_mits_inventory_is_dropped(published):
    assert "mit-1" not in published
    assert published["cam-lind"]["merged_sources"] == "CAMBRIDGE"


def test_a_different_genus_beside_it_is_a_different_tree(published):
    assert {"cam-oak", "mit-2"} <= set(published)


def test_the_next_tree_along_is_kept(published):
    assert {"cam-plane", "mit-3"} <= set(published)


def test_mit_never_marks_its_own_rows(published):
    assert {"mit-4", "mit-5"} <= set(published)


def test_mit_rows_publish_under_their_own_label(published):
    assert published["mit-4"]["merged_sources"] == "MIT"


def test_the_published_flag_is_always_false(published):
    assert all(row["uscam_border_duplicate"] is False for row in published.values())


def test_the_survivors(published):
    assert set(published) == {
        "cam-lind", "cam-oak", "cam-plane", "mit-2", "mit-3", "mit-4", "mit-5",
    }
