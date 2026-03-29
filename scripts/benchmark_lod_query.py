#!/usr/bin/env python3
"""Benchmark z15 detailed LOD query strategies in standalone DuckDB.

Usage:
  C:/Users/ethan/coding_projects/sf_tree_reporting/.venv/Scripts/python.exe scripts/benchmark_lod_query.py
"""

from __future__ import annotations

import statistics
import time
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
RAW_JSON = ROOT / "data" / "raw_data.json"
REMOTE_SPECIES_PARQUET_URL = "https://storage.googleapis.com/trilogy_public_models/duckdb/trees/tree_enrichment_v1.parquet"

WEB_MERCATOR_MAX = 20037508.342789244
WEB_MERCATOR_WORLD = WEB_MERCATOR_MAX * 2
ZOOM = 15


def run_timed(con: duckdb.DuckDBPyConnection, sql: str, runs: int = 5) -> tuple[float, float, float]:
    # warmup
    con.execute(sql).fetchall()
    samples: list[float] = []
    for _ in range(runs):
        t0 = time.perf_counter()
        con.execute(sql).fetchall()
        samples.append((time.perf_counter() - t0) * 1000)
    return statistics.mean(samples), statistics.median(samples), min(samples)


def setup(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        """
        CREATE OR REPLACE TABLE trees AS
        SELECT * FROM read_json_auto(?);
        """,
        [str(RAW_JSON)],
    )

    try:
        con.execute(
            f"""
            CREATE OR REPLACE TABLE species_enrichment AS
            SELECT species, tree_form
            FROM read_parquet('{REMOTE_SPECIES_PARQUET_URL}');
            """
        )
    except duckdb.Error:
        con.execute(
            f"""
            CREATE OR REPLACE TABLE species_enrichment AS
            SELECT
              species,
              CASE lower(trim(COALESCE(tree_category, 'default')))
                WHEN 'coniferous' THEN 'conifer'
                ELSE lower(trim(COALESCE(tree_category, 'default')))
              END AS tree_form
            FROM read_parquet('{REMOTE_SPECIES_PARQUET_URL}');
            """
        )


def build_precomputed(con: duckdb.DuckDBPyConnection) -> float:
    t0 = time.perf_counter()
    con.execute(
        f"""
        CREATE OR REPLACE TABLE trees_fast AS
        WITH joined AS (
          SELECT
            t.tree_id,
            t.species,
            t.latitude,
            t.longitude,
            COALESCE(t.diameter_at_breast_height, 3) AS dbh,
            CASE lower(trim(COALESCE(se.tree_form, 'default')))
              WHEN 'palm' THEN 'palm'
              WHEN 'broadleaf' THEN 'broadleaf'
              WHEN 'conifer' THEN 'conifer'
              WHEN 'spreading' THEN 'spreading'
              WHEN 'columnar' THEN 'columnar'
              WHEN 'ornamental' THEN 'ornamental'
              WHEN 'weeping' THEN 'weeping'
              WHEN 'multi_trunk' THEN 'multi_trunk'
              ELSE 'default'
            END AS category,
            LEAST(GREATEST(t.latitude, -85.05112878), 85.05112878) AS latitude_clamped,
            ((t.longitude + 180.0) / 360.0) AS x_norm,
            ((1.0 - ln(tan(radians(LEAST(GREATEST(t.latitude, -85.05112878), 85.05112878))) +
            (1.0 / cos(radians(LEAST(GREATEST(t.latitude, -85.05112878), 85.05112878))))) / pi()) / 2.0) AS y_norm
          FROM trees t
          LEFT JOIN species_enrichment se
            ON t.species = se.species
          WHERE t.latitude IS NOT NULL
            AND t.longitude IS NOT NULL
        )
        SELECT
          tree_id,
          species,
          TRY_CAST(dbh AS DOUBLE) AS dbh,
          category,
          ((longitude * {WEB_MERCATOR_MAX}) / 180.0) AS x_3857,
          (6378137.0 * ln(tan(pi() / 4.0 + radians(latitude_clamped) / 2.0))) AS y_3857,
          CAST(floor(x_norm * pow(2, 15)) AS INTEGER) AS xtile_z15,
          CAST(floor(y_norm * pow(2, 15)) AS INTEGER) AS ytile_z15,
          CAST(floor(x_norm * pow(2, 17)) AS INTEGER) AS xtile_z17,
          CAST(floor(y_norm * pow(2, 17)) AS INTEGER) AS ytile_z17
        FROM joined;
        """
    )
    return (time.perf_counter() - t0) * 1000


def main() -> None:
    if not RAW_JSON.exists():
        raise SystemExit(f"Missing {RAW_JSON}")

    con = duckdb.connect(database=":memory:")
    con.execute("PRAGMA threads=8")

    setup(con)
    rows = con.execute("SELECT COUNT(*) FROM trees").fetchone()[0]
    print(f"rows={rows}")

    baseline_sql = f"""
    WITH pts AS (
      SELECT
        CAST(
          floor((((longitude * {WEB_MERCATOR_MAX}) / 180.0) + {WEB_MERCATOR_MAX}) / ({WEB_MERCATOR_WORLD} / pow(2, {ZOOM})))
          AS INTEGER
        ) AS xtile,
        CAST(
          floor(({WEB_MERCATOR_MAX} - (6378137.0 * ln(tan(pi() / 4.0 + radians(LEAST(GREATEST(latitude, -85.05112878), 85.05112878)) / 2.0)))) / ({WEB_MERCATOR_WORLD} / pow(2, {ZOOM})))
          AS INTEGER
        ) AS ytile,
        COALESCE(diameter_at_breast_height, 3) AS dbh,
        CASE lower(trim(COALESCE(se.tree_form, 'default')))
          WHEN 'palm' THEN 'palm'
          WHEN 'broadleaf' THEN 'broadleaf'
          WHEN 'conifer' THEN 'conifer'
          WHEN 'spreading' THEN 'spreading'
          WHEN 'columnar' THEN 'columnar'
          WHEN 'ornamental' THEN 'ornamental'
          WHEN 'weeping' THEN 'weeping'
          WHEN 'multi_trunk' THEN 'multi_trunk'
          ELSE 'default'
        END AS category
      FROM trees t
      LEFT JOIN species_enrichment se
        ON lower(trim(t.species)) = lower(trim(se.species))
      WHERE latitude IS NOT NULL AND longitude IS NOT NULL
    )
    SELECT xtile, ytile, COUNT(*) AS n, AVG(dbh) AS avg_dbh
    FROM pts
    GROUP BY 1, 2;
    """

    mean, median, best = run_timed(con, baseline_sql)
    print(f"baseline_z15_detailed_batch_ms mean={mean:.1f} median={median:.1f} best={best:.1f}")

    precompute_ms = build_precomputed(con)
    print(f"precompute_trees_fast_ms={precompute_ms:.1f}")

    precomputed_global_sql = """
    SELECT xtile_z15 AS xtile, ytile_z15 AS ytile, COUNT(*) AS n, AVG(dbh) AS avg_dbh
    FROM trees_fast
    GROUP BY 1, 2;
    """
    mean, median, best = run_timed(con, precomputed_global_sql)
    print(f"precomputed_global_z15_ms mean={mean:.1f} median={median:.1f} best={best:.1f}")

    # 6x6 neighborhood test around SF center-like tile at z17.
    center = con.execute(
        f"""
        SELECT
          CAST(floor(((-122.44 + 180.0) / 360.0) * pow(2, 17)) AS INTEGER) AS cx,
          CAST(floor(((1.0 - ln(tan(radians(37.76)) + 1.0 / cos(radians(37.76))) / pi()) / 2.0) * pow(2, 17)) AS INTEGER) AS cy
        """
    ).fetchone()
    cx, cy = int(center[0]), int(center[1])
    min_x, max_x = cx - 3, cx + 2
    min_y, max_y = cy - 3, cy + 2

    precomputed_neighborhood_sql = f"""
    SELECT xtile_z17 AS xtile, ytile_z17 AS ytile, COUNT(*) AS n, AVG(dbh) AS avg_dbh
    FROM trees_fast
    WHERE xtile_z17 BETWEEN {min_x} AND {max_x}
      AND ytile_z17 BETWEEN {min_y} AND {max_y}
    GROUP BY 1, 2;
    """
    mean, median, best = run_timed(con, precomputed_neighborhood_sql)
    print(f"precomputed_6x6_z17_ms mean={mean:.1f} median={median:.1f} best={best:.1f}")

    # Materialize z15 grouped once, then lookup by tile.
    t0 = time.perf_counter()
    con.execute(
        """
        CREATE OR REPLACE TEMP TABLE z15_tile_stats AS
        SELECT xtile_z15 AS xtile, ytile_z15 AS ytile, COUNT(*) AS n, AVG(dbh) AS avg_dbh
        FROM trees_fast
        GROUP BY 1, 2;
        """
    )
    z15_build_ms = (time.perf_counter() - t0) * 1000
    print(f"materialize_z15_tile_stats_ms={z15_build_ms:.1f}")

    center15 = con.execute(
        """
        SELECT
          CAST(floor(((-122.44 + 180.0) / 360.0) * pow(2, 15)) AS INTEGER) AS cx,
          CAST(floor(((1.0 - ln(tan(radians(37.76)) + 1.0 / cos(radians(37.76))) / pi()) / 2.0) * pow(2, 15)) AS INTEGER) AS cy
        """
    ).fetchone()
    c15x, c15y = int(center15[0]), int(center15[1])

    lookup_sql = f"""
    SELECT *
    FROM z15_tile_stats
    WHERE xtile BETWEEN {c15x - 3} AND {c15x + 2}
      AND ytile BETWEEN {c15y - 3} AND {c15y + 2};
    """
    mean, median, best = run_timed(con, lookup_sql)
    print(f"materialized_lookup_ms mean={mean:.1f} median={median:.1f} best={best:.1f}")

    # Optional: benchmark full MVT assembly, which is closer to map runtime cost.
    spatial_ok = True
    try:
      con.execute("LOAD spatial;")
    except Exception:
      try:
        con.execute("INSTALL spatial;")
        con.execute("LOAD spatial;")
      except Exception:
        spatial_ok = False

    if spatial_ok:
      mvt_precomputed_sql = """
      WITH rows AS (
        SELECT
          xtile_z15 AS xtile,
          ytile_z15 AS ytile,
          {
            'geom': ST_AsMVTGeom(
              ST_Point(x_3857, y_3857),
              ST_Extent(ST_TileEnvelope(15, xtile_z15, ytile_z15)),
              4096,
              64,
              true
            ),
            'id': tree_id,
            'dbh': dbh,
            'category': category,
            'rotation': 0
          } AS feature
        FROM trees_fast
      )
      SELECT
        xtile,
        ytile,
        ST_AsMVT(feature, 'trees', 4096, 'geom') AS mvt
      FROM rows
      WHERE feature.geom IS NOT NULL AND NOT ST_IsEmpty(feature.geom)
      GROUP BY xtile, ytile;
      """
      mean, median, best = run_timed(con, mvt_precomputed_sql, runs=3)
      print(f"mvt_precomputed_global_z15_ms mean={mean:.1f} median={median:.1f} best={best:.1f}")

      mvt_neighborhood_sql = f"""
      WITH rows AS (
        SELECT
          xtile_z15 AS xtile,
          ytile_z15 AS ytile,
          {{
            'geom': ST_AsMVTGeom(
              ST_Point(x_3857, y_3857),
              ST_Extent(ST_TileEnvelope(15, xtile_z15, ytile_z15)),
              4096,
              64,
              true
            ),
            'id': tree_id,
            'dbh': dbh,
            'category': category,
            'rotation': 0
          }} AS feature
        FROM trees_fast
        WHERE xtile_z15 BETWEEN {c15x - 3} AND {c15x + 2}
          AND ytile_z15 BETWEEN {c15y - 3} AND {c15y + 2}
      )
      SELECT
        xtile,
        ytile,
        ST_AsMVT(feature, 'trees', 4096, 'geom') AS mvt
      FROM rows
      WHERE feature.geom IS NOT NULL AND NOT ST_IsEmpty(feature.geom)
      GROUP BY xtile, ytile;
      """
      mean, median, best = run_timed(con, mvt_neighborhood_sql)
      print(f"mvt_precomputed_6x6_z15_ms mean={mean:.1f} median={median:.1f} best={best:.1f}")

      t0 = time.perf_counter()
      con.execute(
        """
        CREATE OR REPLACE TEMP TABLE z15_features AS
        SELECT
          xtile_z15 AS xtile,
          ytile_z15 AS ytile,
          {
            'geom': ST_AsMVTGeom(
              ST_Point(x_3857, y_3857),
              ST_Extent(ST_TileEnvelope(15, xtile_z15, ytile_z15)),
              4096,
              64,
              true
            ),
            'id': tree_id,
            'dbh': dbh,
            'category': category,
            'rotation': 0
          } AS feature
        FROM trees_fast;
        """
      )
      z15_features_ms = (time.perf_counter() - t0) * 1000
      print(f"materialize_z15_features_ms={z15_features_ms:.1f}")

      mvt_from_features_6x6_sql = f"""
      SELECT
        xtile,
        ytile,
        ST_AsMVT(feature, 'trees', 4096, 'geom') AS mvt
      FROM z15_features
      WHERE xtile BETWEEN {c15x - 3} AND {c15x + 2}
        AND ytile BETWEEN {c15y - 3} AND {c15y + 2}
        AND feature.geom IS NOT NULL
        AND NOT ST_IsEmpty(feature.geom)
      GROUP BY xtile, ytile;
      """
      mean, median, best = run_timed(con, mvt_from_features_6x6_sql)
      print(f"mvt_from_features_6x6_z15_ms mean={mean:.1f} median={median:.1f} best={best:.1f}")

      t0 = time.perf_counter()
      con.execute(
        """
        CREATE OR REPLACE TEMP TABLE z15_mvt_tiles AS
        WITH rows AS (
          SELECT
            xtile_z15 AS xtile,
            ytile_z15 AS ytile,
            {
              'geom': ST_AsMVTGeom(
                ST_Point(x_3857, y_3857),
                ST_Extent(ST_TileEnvelope(15, xtile_z15, ytile_z15)),
                4096,
                64,
                true
              ),
              'id': tree_id,
              'dbh': dbh,
              'category': category,
              'rotation': 0
            } AS feature
          FROM trees_fast
        )
        SELECT
          xtile,
          ytile,
          ST_AsMVT(feature, 'trees', 4096, 'geom') AS mvt
        FROM rows
        WHERE feature.geom IS NOT NULL AND NOT ST_IsEmpty(feature.geom)
        GROUP BY xtile, ytile;
        """
      )
      mvt_build_ms = (time.perf_counter() - t0) * 1000
      print(f"materialize_z15_mvt_tiles_ms={mvt_build_ms:.1f}")

      mvt_lookup_sql = f"""
      SELECT COUNT(*)
      FROM z15_mvt_tiles
      WHERE xtile BETWEEN {c15x - 3} AND {c15x + 2}
        AND ytile BETWEEN {c15y - 3} AND {c15y + 2};
      """
      mean, median, best = run_timed(con, mvt_lookup_sql)
      print(f"materialized_mvt_lookup_6x6_ms mean={mean:.1f} median={median:.1f} best={best:.1f}")
    else:
      print("mvt_precomputed_global_z15_ms skipped (spatial extension unavailable)")


if __name__ == "__main__":
    main()
