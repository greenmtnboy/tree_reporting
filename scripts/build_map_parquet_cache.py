#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

import duckdb


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
PUBLIC_CACHE_DIR = ROOT / "src" / "public" / "data" / "cache"

RAW_JSON = DATA_DIR / "raw_data.json"
REMOTE_SPECIES_PARQUET_URL = "https://storage.googleapis.com/trilogy_public_models/duckdb/trees/tree_enrichment_v1.parquet"


def build() -> None:
    if not RAW_JSON.exists():
        raise SystemExit(f"Missing {RAW_JSON}")

    PUBLIC_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    con = duckdb.connect(database=":memory:")
    con.execute("PRAGMA threads=8")

    con.execute("CREATE OR REPLACE TABLE trees AS SELECT * FROM read_json_auto(?)", [str(RAW_JSON)])

    try:
        con.execute(
            f"""
            CREATE OR REPLACE TABLE species_enrichment AS
            SELECT
              species,
              common_names,
              tree_form,
              native_status,
              is_evergreen,
              mature_height_ft,
              bloom_season,
              wildlife_value,
              fire_risk,
              drought_tolerance
            FROM read_parquet('{REMOTE_SPECIES_PARQUET_URL}')
            """
        )
    except duckdb.Error:
        con.execute(
            f"""
            CREATE OR REPLACE TABLE species_enrichment AS
            SELECT
              species,
              common_names,
              CASE lower(trim(COALESCE(tree_category, 'default')))
                WHEN 'coniferous' THEN 'conifer'
                ELSE lower(trim(COALESCE(tree_category, 'default')))
              END AS tree_form,
              native_status,
              is_evergreen,
              mature_height_ft,
              bloom_season,
              wildlife_value,
              fire_risk,
              drought_tolerance
            FROM read_parquet('{REMOTE_SPECIES_PARQUET_URL}')
            """
        )

    # Persist base parquet caches.
    con.execute(f"COPY trees TO '{(PUBLIC_CACHE_DIR / 'trees.parquet').as_posix()}' (FORMAT PARQUET)")

    # Precomputed map-optimized table.
    con.execute(
        """
        CREATE OR REPLACE TABLE trees_fast AS
        WITH joined AS (
          SELECT
            t.tree_id,
            t.common_name,
            t.site_info,
            t.plant_date,
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
            END AS tree_form,
            se.native_status,
            se.is_evergreen,
            se.mature_height_ft,
            se.bloom_season,
            se.wildlife_value,
            se.fire_risk,
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
          common_name,
          site_info,
          plant_date,
          species,
          latitude,
          longitude,
          TRY_CAST(dbh AS DOUBLE) AS dbh,
          tree_form,
          native_status,
          is_evergreen,
          mature_height_ft,
          bloom_season,
          wildlife_value,
          fire_risk,
          ((longitude * 20037508.342789244) / 180.0) AS x_3857,
          (6378137.0 * ln(tan(pi() / 4.0 + radians(latitude_clamped) / 2.0))) AS y_3857,
          CAST(floor(x_norm * pow(2, 13)) AS INTEGER) AS xtile_z13,
          CAST(floor(y_norm * pow(2, 13)) AS INTEGER) AS ytile_z13,
          CAST(floor(x_norm * pow(2, 14)) AS INTEGER) AS xtile_z14,
          CAST(floor(y_norm * pow(2, 14)) AS INTEGER) AS ytile_z14,
          CAST(floor(x_norm * pow(2, 15)) AS INTEGER) AS xtile_z15,
          CAST(floor(y_norm * pow(2, 15)) AS INTEGER) AS ytile_z15,
          CAST(floor(x_norm * pow(2, 16)) AS INTEGER) AS xtile_z16,
          CAST(floor(y_norm * pow(2, 16)) AS INTEGER) AS ytile_z16,
          CAST(floor(x_norm * pow(2, 17)) AS INTEGER) AS xtile_z17,
          CAST(floor(y_norm * pow(2, 17)) AS INTEGER) AS ytile_z17,
          CAST(floor(x_norm * pow(2, 18)) AS INTEGER) AS xtile_z18,
          CAST(floor(y_norm * pow(2, 18)) AS INTEGER) AS ytile_z18,
          CAST(floor(x_norm * pow(2, 19)) AS INTEGER) AS xtile_z19,
          CAST(floor(y_norm * pow(2, 19)) AS INTEGER) AS ytile_z19,
          CAST(floor(x_norm * pow(2, 20)) AS INTEGER) AS xtile_z20,
          CAST(floor(y_norm * pow(2, 20)) AS INTEGER) AS ytile_z20
        FROM joined
        """
    )
    con.execute(f"COPY trees_fast TO '{(PUBLIC_CACHE_DIR / 'trees_fast.parquet').as_posix()}' (FORMAT PARQUET)")

    for z, grid in ((13, 64), (14, 32)):
        con.execute(
            f"""
            CREATE OR REPLACE TABLE agg_z{z}_cache AS
            SELECT
              xtile_z{z} AS xtile,
              ytile_z{z} AS ytile,
              floor(x_3857 / {grid}) * {grid} + {grid} / 2.0 AS gx,
              floor(y_3857 / {grid}) * {grid} + {grid} / 2.0 AS gy,
              AVG(dbh) AS dbh,
              COUNT(*) AS point_count
            FROM trees_fast
            GROUP BY 1, 2, 3, 4
            """
        )
        con.execute(
            f"COPY agg_z{z}_cache TO '{(PUBLIC_CACHE_DIR / f'agg_z{z}.parquet').as_posix()}' (FORMAT PARQUET)"
        )

    print(f"Wrote parquet cache files under {PUBLIC_CACHE_DIR}")


if __name__ == "__main__":
    build()
