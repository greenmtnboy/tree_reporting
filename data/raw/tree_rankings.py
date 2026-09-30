#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["duckdb", "pyarrow", "pytrilogy", "requests"]
# ///
"""City rankings over published, deduplicated inventories. Emits Arrow IPC.

Rarity is species frequency among identified trees, not conservation status.
Size ranks use recorded measurements only; ties share a rank. Null, zero,
negative and non-finite measurements never receive a rank.
"""
import sys

import duckdb
import pyarrow as pa

from shared.ingest import SPECIES_SENTINELS


def build_rankings(con: duckdb.DuckDBPyConnection) -> pa.Table:
    """Read `trees` and species-keyed `enrichment` relations on this connection."""
    sentinels = ",".join("'" + s.lower().replace("'", "''") + "'" for s in SPECIES_SENTINELS)
    return con.execute(f"""
        WITH clean AS (
            SELECT tree_id, city, species, latitude, longitude,
                nullif(trim(species), '') IS NOT NULL
                    AND lower(trim(species)) NOT IN ({sentinels}) AS identified,
                CASE WHEN isfinite(diameter_at_breast_height) AND diameter_at_breast_height > 0
                     THEN diameter_at_breast_height END AS dbh,
                CASE WHEN isfinite(crown_width_m) AND crown_width_m > 0
                     THEN crown_width_m END AS crown
            FROM trees
        ), counted AS (
            SELECT *, count(*) FILTER (WHERE identified) OVER (PARTITION BY city) AS identified_city_count,
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
    """).to_arrow_table()


if __name__ == '__main__':
    from enrichment._tree_shared import TREE_INFO_PARQUET, ENRICHMENT_PARQUET

    with duckdb.connect() as con:
        con.from_parquet(TREE_INFO_PARQUET).create_view('trees')
        con.from_parquet(ENRICHMENT_PARQUET).create_view('enrichment')
        table = build_rankings(con)
        with pa.ipc.new_stream(sys.stdout.buffer, table.schema) as writer:
            writer.write_table(table)
