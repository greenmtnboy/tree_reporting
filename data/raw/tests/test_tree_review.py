"""The review file: trees whose recorded values look wrong (city_derived.preql).

San Francisco's derived model plans its `ussfo_tree_review` target as the
refresh would, and the SQL is run over a fixture parquet of the city's trees.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

RAW_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAW_DIR))

MODEL = RAW_DIR / "ussfo" / "sf_tree_derived.preql"

# 30 measured Acer platanoides averaging ~11 in, so 3x the mean is ~33 in.
MAPLES = [(f"sf-m{i}", "Acer platanoides", 10.0, "DPW") for i in range(30)]
TREES = MAPLES + [
    ("sf-big", "Acer platanoides", 50.0, "DPW"),          # 30"+ and over 3x the mean: flagged
    ("sf-29", "Acer platanoides", 29.0, "DPW"),           # over 2x the mean, under the 30" floor
    ("sf-none", "Acer platanoides", None, "DPW"),         # no diameter
    ("sf-rare", "Ficus microcarpa", 60.0, "DPW"),         # too few of its species measured
    ("sf-genus", "Ficus", 80.0, "DPW"),                   # a genus mixes species
    ("sf-unknown", "Unknown deciduous", 80.0, "DPW"),     # a sentinel
    ("pred-1", "Acer platanoides", 60.0, "PREDICTED_USSFO"),  # an imagery detection's diameter is the model's
] + [(f"sf-f{i}", "Ficus", 10.0, "DPW") for i in range(30)] + [
    (f"sf-u{i}", "Unknown deciduous", 10.0, "DPW") for i in range(30)
]


def review_sql() -> str:
    from trilogy import Dialects, Environment
    from trilogy.dialect.config import DuckDBConfig
    from trilogy.execution.state.isolation import hidden_datasources

    env = Environment(working_path=MODEL.parent)
    env.parse(MODEL.read_text(encoding="utf-8"))
    executor = Dialects.DUCK_DB.default_executor(environment=env, conf=DuckDBConfig(enable_python_datasources=True))
    datasource = env.datasources["ussfo_tree_review"]
    with hidden_datasources(env, {"ussfo_tree_review"}):
        statement = datasource.create_update_statement(env, None, line_no=None)
        return "\n".join(executor.generator.compile_statement(q)
                         for q in executor.generator.generate_queries(env, [statement]))


@pytest.fixture(scope="module")
def flagged(tmp_path_factory) -> dict[str, dict]:
    import duckdb

    tmp = tmp_path_factory.mktemp("review")
    conn = duckdb.connect()
    conn.execute(
        """
        CREATE TABLE trees (
            tree_id VARCHAR, city VARCHAR, data_source VARCHAR, species VARCHAR, cultivar VARCHAR,
            crown_width_m DOUBLE, tree_name VARCHAR, plant_date DATE, diameter_at_breast_height DOUBLE,
            latitude DOUBLE, longitude DOUBLE, submission_photo_url VARCHAR
        )
        """
    )
    conn.executemany(
        "INSERT INTO trees VALUES (?, 'USSFO', ?, ?, NULL, NULL, NULL, NULL, ?, 37.77, -122.42, NULL)",
        [(tree_id, source, species, dbh) for tree_id, species, dbh, source in TREES],
    )
    path = tmp / "trees.parquet"
    conn.execute(f"COPY trees TO '{path.as_posix()}' (FORMAT PARQUET)")

    sql = review_sql()
    sql = re.sub(r'^.*?INSERT INTO "[^"]*"\s*', "", sql, count=1, flags=re.S)
    read = set(re.findall(r"trees/([a-z_]+)_v\d+\.parquet", sql))
    # The trees from the city's parquet; the watermark from the projection the
    # same job wrote just before (the refresh's declared order).
    assert read <= {"ussfo_tree_info", "ussfo_tree_browser"}, read
    sql = re.sub(r"read_parquet\('[^']*ussfo_tree_info_v[^']*'\)", f"read_parquet('{path.as_posix()}')", sql)
    stamp = "TIMESTAMPTZ '2026-11-01 00:00:00+00'"
    sql = re.sub(r"read_parquet\('[^']*ussfo_tree_browser_v[^']*'\)",
                 f"(SELECT {stamp} AS prediction_inputs_published_through)", sql)
    sql = re.sub(r"uv_run\('[^']*_derived_probe\.py'[^)]*\)", f"(SELECT {stamp} AS data_updated_through)", sql)
    model = (RAW_DIR / "city_derived.preql").read_text(encoding="utf-8")
    for name, value in re.findall(r"^const (review_\w+) <- ([-\d.]+);", model, re.M):
        sql = re.sub(rf":{name}\b", value, sql)
    assert "read_parquet('https" not in sql and "uv_run" not in sql, sql
    cursor = conn.execute(sql)
    columns = [d[0] for d in cursor.description]
    return {row[0]: dict(zip(columns, row)) for row in cursor.fetchall()}


def test_only_an_outlier_for_its_species_is_flagged(flagged):
    assert set(flagged) == {"sf-big"}, sorted(flagged)
    row = flagged["sf-big"]
    assert row["review_flag"] == "dbh_outlier"
    assert row["diameter_at_breast_height"] == 50.0
    assert row["review_species_mean_dbh"] == pytest.approx((30 * 10 + 50 + 29) / 32)


def test_the_review_file_is_wired_for_every_city():
    from shared.ingest import MUNICIPAL_DATA_SOURCES

    for code in MUNICIPAL_DATA_SOURCES:
        (path,) = (RAW_DIR / code.lower()).glob("*_tree_derived.preql")
        text = path.read_text(encoding="utf-8")
        lc = code.lower()
        assert f"datasource {lc}_tree_review extends city_tree_review ()" in text, code
        assert f"{lc}_tree_review_v{{data_version}}.parquet" in text, code


def test_the_card_reads_columns_the_file_has():
    """src/src/lib/treeReview.ts names the file's columns, not the model's concepts."""
    from trilogy import Environment

    env = Environment(working_path=MODEL.parent)
    env.parse(MODEL.read_text(encoding="utf-8"))
    columns = {c.alias for c in env.datasources["ussfo_tree_review"].columns}
    client = (RAW_DIR.parents[1] / "src" / "src" / "lib" / "treeReview.ts").read_text(encoding="utf-8")
    (select,) = re.findall(r"SELECT (.+?) FROM read_parquet", client)
    read = {name.strip() for name in select.split(",")} | {"tree_id"}
    assert read <= columns, read - columns
