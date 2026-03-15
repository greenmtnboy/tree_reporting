#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["duckdb"]
# ///
# Freshness probe for tree_enrichment datasource.
# Prints "true" if every species in tree_info is completely enriched, "false" otherwise.

import sys

import duckdb
from _tree_shared import (
    ENRICHMENT_PARQUET,
    TREE_INFO_PARQUET,
    SKIP_SPECIES,
    SPECIES_EXCLUSION_SQL,
)

_COMPLETENESS_EXPR = (
    "common_names IS NOT NULL AND trim(common_names) != ''"
    " AND tree_category IS NOT NULL"
)

def main() -> None:
    conn = duckdb.connect()

    try:
        all_species: set[str] = {
            row[0]
            for row in conn.execute(
                f"""
                SELECT DISTINCT species
                FROM read_parquet(?)
                WHERE {SPECIES_EXCLUSION_SQL}
                """,
                [TREE_INFO_PARQUET],
            ).fetchall()
        }
        all_species.difference_update(SKIP_SPECIES)
        try:
            complete_enriched: set[str] = {
                row[0]
                for row in conn.execute(
                    f"SELECT species FROM read_parquet(?) WHERE species IS NOT NULL AND {_COMPLETENESS_EXPR}",
                    [ENRICHMENT_PARQUET],
                ).fetchall()
            }
        except Exception:
            # Enrichment parquet missing or unreadable — treat as fully stale.
            complete_enriched = set()
    finally:
        conn.close()
    if all_species - complete_enriched:
        print(
                    f"missing enrichment for species: {all_species - complete_enriched}",
                    file=sys.stderr,
                )
    print("true" if all_species <= complete_enriched else "false")

if __name__ == "__main__":
    main()
