#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["duckdb", "pyarrow", "pytrilogy>=0.3.391", "requests"]
# ///
"""City rankings over published, deduplicated inventories. Emits Arrow IPC.

Rarity is species frequency among identified trees, not conservation status.
Size ranks use recorded measurements only; ties share a rank. Null, zero,
negative and non-finite measurements never receive a rank.
"""
import os
import sys
import tempfile

import duckdb
import pyarrow as pa

from shared.ingest import SPECIES_SENTINELS


# Rows per Arrow batch written to stdout: the whole ranked rollup (12M+ rows)
# never sits in Python at once.
BATCH_ROWS = 256_000


def build_rankings(con: duckdb.DuckDBPyConnection) -> duckdb.DuckDBPyRelation:
    """Read `trees` and species-keyed `enrichment` relations on this connection.

    Lazy: nothing runs until the caller fetches, so the job can stream it."""
    sentinels = ",".join("'" + s.lower().replace("'", "''") + "'" for s in SPECIES_SENTINELS)
    # The crown-width rollout is incremental. An older published rollup can
    # still support trunk/rarity missions without claiming any canopy winners.
    crown = 'crown_width_m' if 'crown_width_m' in con.table('trees').columns else 'NULL::DOUBLE'
    return con.sql(f"""
        WITH clean AS (
            SELECT tree_id, city, species, latitude, longitude,
                nullif(trim(species), '') IS NOT NULL
                    AND lower(trim(species)) NOT IN ({sentinels}) AS identified,
                CASE WHEN isfinite(diameter_at_breast_height) AND diameter_at_breast_height > 0
                     THEN diameter_at_breast_height END AS dbh,
                CASE WHEN isfinite({crown}) AND {crown} > 0
                     THEN {crown} END AS crown
            FROM trees
        ), counted AS (
            -- Not `count(*) FILTER (WHERE identified) OVER (PARTITION BY city)`:
            -- DuckDB evaluates a FILTERed whole-partition window per row, which is
            -- quadratic in city size and ran past the job's 30-minute timeout at
            -- 11.8M trees. The unfiltered sum takes the once-per-partition path.
            SELECT *, sum(identified::BIGINT) OVER (PARTITION BY city)::BIGINT AS identified_city_count,
                CASE WHEN identified THEN count(*) OVER (PARTITION BY city, species) END AS species_city_count
            FROM clean
        )
        SELECT tree_id AS ranking_tree_id, city AS ranking_city, species AS ranking_species,
            latitude AS ranking_latitude, longitude AS ranking_longitude,
            e.tree_form AS ranking_tree_form, species_city_count, identified_city_count,
            species_city_count::DOUBLE / nullif(identified_city_count, 0) AS species_city_share,
            CASE WHEN NOT identified THEN NULL
                 WHEN species_city_count * 100 <= identified_city_count THEN 'rare'
                 WHEN species_city_count * 20 <= identified_city_count THEN 'unusual'
                 ELSE 'common' END AS rarity_tier,
            CASE WHEN dbh IS NOT NULL THEN rank() OVER (PARTITION BY city ORDER BY dbh DESC NULLS LAST) END AS trunk_rank,
            CASE WHEN crown IS NOT NULL THEN rank() OVER (PARTITION BY city ORDER BY crown DESC NULLS LAST) END AS canopy_rank
        FROM counted LEFT JOIN enrichment e USING (species)
        ORDER BY city, tree_id
    """)


if __name__ == '__main__':
    from enrichment._tree_shared import TREE_INFO_PARQUET, ENRICHMENT_PARQUET

    # The job was OOM-killed at its container limit (exit 137) holding the
    # whole result as one Arrow table plus a copy. Stream it in batches, and
    # give DuckDB a cap below the container's and a disk to spill the windows
    # and the sort to, so it pages instead of being killed. RANKINGS_MEMORY_LIMIT
    # overrides the cap.
    with duckdb.connect() as con, tempfile.TemporaryDirectory(prefix='tree_rankings_') as spill:
        con.execute(f"SET temp_directory = '{spill}'")
        con.execute(f"SET memory_limit = '{os.environ.get('RANKINGS_MEMORY_LIMIT', '2GB')}'")
        con.from_parquet(TREE_INFO_PARQUET).create_view('trees')
        con.from_parquet(ENRICHMENT_PARQUET).create_view('enrichment')
        reader = build_rankings(con).to_arrow_reader(BATCH_ROWS)
        with pa.ipc.new_stream(sys.stdout.buffer, reader.schema) as writer:
            for batch in reader:
                writer.write_batch(batch)
