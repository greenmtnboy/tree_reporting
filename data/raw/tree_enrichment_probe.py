#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["duckdb","pytrilogy"]
# ///
# Freshness probe for tree_enrichment datasource.
# Prints "true" if every species in tree_info is completely enriched, "false" otherwise.

import sys

import duckdb
from enrichment._tree_shared import (
    ENRICHMENT_PARQUET,
    TREE_INFO_PARQUET,
    SKIP_SPECIES,
    SPECIES_EXCLUSION_SQL,
)

_COMPLETENESS_EXPRESSIONS = [
    (
        "common_names IS NOT NULL"
        " AND array_length(common_names) > 0"
        " AND tree_form IS NOT NULL"
    ),
    (
        "common_names IS NOT NULL AND trim(common_names) != ''"
        " AND tree_form IS NOT NULL"
    ),
]


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

        complete_enriched: set[str] | None = None
        for completeness_expr in _COMPLETENESS_EXPRESSIONS:
            try:
                complete_enriched = {
                    row[0]
                    for row in conn.execute(
                        f"SELECT species FROM read_parquet(?) WHERE species IS NOT NULL AND {completeness_expr}",
                        [ENRICHMENT_PARQUET],
                    ).fetchall()
                }
                break
            except Exception:
                continue

        if complete_enriched is None:
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
