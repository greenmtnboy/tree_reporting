"""Melbourne's border dedup between neighbouring councils' inventories.

Melbourne's own build SQL, planned by pytrilogy and run against fixture rows,
the way test_predicted_reconciliation.py runs Boston's.  The rule
(aumel/melbourne_tree_info.preql, docs/BORDER_DEDUP.md): a council row is
dropped when an earlier-wired council's row of the same genus shares one of
four staggered 3 m cells with it.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

RAW_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAW_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

MODEL = RAW_DIR / "aumel" / "melbourne_tree_info.preql"
M_PER_DEG_LAT = 111_320.0

# Street corners well apart, inside AUMEL's bounds.
P1 = (-37.8000, 144.9800)
P2 = (-37.8050, 144.9850)
P3 = (-37.8100, 144.9900)
P4 = (-37.8150, 144.9950)
P5 = (-37.8200, 145.0000)


def north(point, metres):
    return (point[0] + metres / M_PER_DEG_LAT, point[1])


# (tree_id, data_source, species, point)
COUNCILS = {
    "melbourne_tree_info.py": [
        ("mel-elm", "MELBOURNE_OPENDATA", "Ulmus", P1),           # genus rank, as Melbourne publishes
        ("mel-elm-2", "MELBOURNE_OPENDATA", "Ulmus", P2),
        ("mel-gum", "MELBOURNE_OPENDATA", "Corymbia maculata", P3),
        ("mel-plane", "MELBOURNE_OPENDATA", "Platanus x acerifolia", P5),
    ],
    "port_phillip_tree_info.py": [
        ("pp-oak", "PORT_PHILLIP", "Quercus robur", P4),
    ],
    "yarra_tree_info.py": [
        # The same elm, 1.2 m off and named to species: dropped.
        ("yar-elm", "YARRA", "Ulmus minor", north(P1, 1.2)),
        # Another genus 1.2 m from Melbourne's elm: a different tree, kept.
        ("yar-oak", "YARRA", "Quercus palustris", north(P2, 1.2)),
        # Same genus but 8 m away: the next tree along, kept.
        ("yar-gum", "YARRA", "Corymbia maculata", north(P3, 8.0)),
        # Port Phillip is wired before Yarra, so Port Phillip's oak wins.
        ("yar-oak-2", "YARRA", "Quercus robur", north(P4, 1.2)),
        # Two Yarra planes 1.2 m apart and none of Melbourne's: one inventory
        # never marks its own rows.
        ("yar-plane-a", "YARRA", "Platanus x acerifolia", north(P5, 20.0)),
        ("yar-plane-b", "YARRA", "Platanus x acerifolia", north(P5, 21.2)),
        # No species: never matches.
        ("yar-unknown", "YARRA", None, north(P5, 0.5)),
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
            "INSERT INTO src VALUES (?, 'AUMEL', ?, ?, NULL, NULL, NULL, ?, ?, NULL, NULL, NULL, NULL, NULL, NULL)",
            [(tid, source, species, *point) for tid, source, species, point in rows],
        )
    conn.execute(f"COPY src TO '{path.as_posix()}' (FORMAT PARQUET)")


@pytest.fixture(scope="module")
def published(tmp_path_factory) -> dict[str, dict]:
    import duckdb
    from trilogy import Dialects, Environment
    from trilogy.dialect.config import DuckDBConfig

    from test_satellite_reconciliation import write_modifications

    tmp = tmp_path_factory.mktemp("border_dedup_melbourne")
    conn = duckdb.connect()
    scripts = [
        "melbourne_tree_info.py", "port_phillip_tree_info.py", "yarra_tree_info.py",
        "glen_eira_tree_info.py", "hobsons_bay_tree_info.py", "brimbank_tree_info.py",
        "manningham_tree_info.py", "community_tree_info.py",
        "aumel_osm_staging.parquet",
    ]
    fixtures = {}
    for script in scripts:
        fixtures[script] = tmp / f"{script}.parquet"
        write_rows(conn, fixtures[script], COUNCILS.get(script, []))
    fixtures["tree_modification_info.py"] = tmp / "modifications.parquet"
    write_modifications(conn, fixtures["tree_modification_info.py"], [])

    env = Environment(working_path=MODEL.parent)
    env.parse(MODEL.read_text(encoding="utf-8"))
    executor = Dialects.DUCK_DB.default_executor(
        environment=env, conf=DuckDBConfig(enable_python_datasources=True)
    )
    sql = executor.update_datasource(env.datasources["melbourne_tree_info"], dry_run=True)
    assert sql is not None
    sql = re.sub(r'^.*?INSERT INTO "[^"]*"\s*', "", sql, count=1, flags=re.S)
    assert sql.lstrip().upper().startswith("WITH"), sql[:200]

    stamp = "TIMESTAMPTZ '2026-01-01 00:00:00+00'"
    probe = "(SELECT " + ", ".join(
        f"{stamp} AS {col}"
        for col in ("data_updated_through", "aumel_community_data_updated_through")
    ) + ")"

    def repoint(match: re.Match) -> str:
        script = Path(match.group(1)).name
        if script in fixtures:
            return f"read_parquet('{fixtures[script].as_posix()}')"
        assert script.endswith(("_update_time.py", "_probe.py")), script
        return probe

    sql = re.sub(r"uv_run\('([^']+)'(?:,\s*args := '(?:[^']|'')*')?\)", repoint, sql)
    sql = re.sub(
        r"read_parquet\('[^']*aumel_osm_staging\.parquet[^']*'\)",
        f"read_parquet('{fixtures['aumel_osm_staging.parquet'].as_posix()}')",
        sql,
    )
    assert "uv_run" not in sql and "storage.googleapis.com" not in sql, sql
    cursor = conn.execute(sql)
    columns = [d[0] for d in cursor.description]
    return {row["tree_id"]: row for row in (dict(zip(columns, r)) for r in cursor.fetchall())}


def test_the_same_tree_in_a_later_council_is_dropped(published):
    assert "yar-elm" not in published
    assert "mel-elm" in published


def test_the_earlier_council_wins_whichever_it_is(published):
    assert "pp-oak" in published
    assert "yar-oak-2" not in published


def test_a_different_genus_beside_it_is_a_different_tree(published):
    assert {"mel-elm-2", "yar-oak"} <= set(published)


def test_the_next_tree_along_is_kept(published):
    assert {"mel-gum", "yar-gum"} <= set(published)


def test_one_council_never_marks_its_own_rows(published):
    assert {"yar-plane-a", "yar-plane-b", "mel-plane"} <= set(published)


def test_a_row_without_a_species_never_matches(published):
    assert "yar-unknown" in published


def test_the_published_flag_is_always_false(published):
    assert all(row["aumel_border_duplicate"] is False for row in published.values())


def test_the_survivors(published):
    assert set(published) == {
        "mel-elm", "mel-elm-2", "mel-gum", "mel-plane", "pp-oak",
        "yar-oak", "yar-gum", "yar-plane-a", "yar-plane-b", "yar-unknown",
    }
