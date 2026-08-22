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
    ENRICHMENT_COMPLETE_SQL,
    ENRICHMENT_PARQUET,
    TREE_INFO_PARQUET,
    SPECIES_EXCLUSION_SQL,
    is_enrichable_species,
)

# The current definition, shared with tree_enrichment.py so the run and the
# probe cannot disagree about what it is still owed, followed by the legacy
# one for a parquet whose `common_names` is still a comma-joined string.
_COMPLETENESS_EXPRESSIONS = [
    ENRICHMENT_COMPLETE_SQL,
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
        # The SQL above filters the values named in SKIP_SPECIES; this is the
        # general rule, and it is the same one tree_enrichment.py queues from.
        all_species = {s for s in all_species if is_enrichable_species(s)}

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
