#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["duckdb", "pyarrow"]
# ///
"""Backfill tree_enrichment.parquet to use scientific-name-only keys.

The old format stored species as "Genus species :: Common Name" (SF convention).
This script rewrites the parquet so every species key is just the scientific
prefix (everything before "::"), deduplicating when multiple old keys share the
same scientific name (most-complete / most-recently-enriched row wins).

Run once after the species-key refactor.  Safe to re-run — idempotent.

Usage:
    uv run backfill_enrichment_keys.py
    uv run backfill_enrichment_keys.py --dry-run   # print row counts only, no upload
"""
import argparse
import subprocess
import sys

import duckdb
import pyarrow.parquet as pq

from _tree_shared import ENRICHMENT_PARQUET, ENRICHMENT_GCS_URI

OUTPUT_PATH = "tree_enrichment.parquet"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print row counts and sample output but do not write or upload.",
    )
    parser.add_argument(
        "--source",
        default=ENRICHMENT_PARQUET,
        help=f"Parquet source to read (default: {ENRICHMENT_PARQUET})",
    )
    args = parser.parse_args()

    conn = duckdb.connect()

    print(f"[info] reading from {args.source}", file=sys.stderr)

    before_count = conn.execute(
        f"SELECT COUNT(*) FROM read_parquet('{args.source}')"
    ).fetchone()[0]
    print(f"[info] source rows: {before_count}", file=sys.stderr)

    # Strip ":: common name" suffix from species key, deduplicate by keeping the
    # most-complete then most-recently-enriched row per scientific name.
    result = conn.execute(
        f"""
        WITH split_keys AS (
            SELECT
                trim(string_split(species, '::')[1]) AS scientific_name,
                common_names,
                native_status,
                is_evergreen,
                mature_height_ft,
                canopy_spread_ft,
                growth_rate,
                lifespan_years,
                drought_tolerance,
                bloom_season,
                wildlife_value,
                fire_risk,
                tree_category,
                (common_names IS NOT NULL AND trim(common_names) != ''
                 AND native_status IS NOT NULL AND is_evergreen IS NOT NULL
                 AND mature_height_ft IS NOT NULL AND canopy_spread_ft IS NOT NULL
                 AND growth_rate IS NOT NULL AND drought_tolerance IS NOT NULL
                 AND bloom_season IS NOT NULL AND wildlife_value IS NOT NULL
                 AND tree_category IS NOT NULL) AS is_complete_flag,
                icon_rgba_b64,
                icon_width,
                icon_height,
                enriched_at
            FROM read_parquet('{args.source}')
            WHERE species IS NOT NULL AND trim(species) != ''
        ),
        ranked AS (
            SELECT *,
                ROW_NUMBER() OVER (
                    PARTITION BY scientific_name
                    ORDER BY is_complete_flag DESC, enriched_at DESC
                ) AS rn
            FROM split_keys
            WHERE scientific_name IS NOT NULL AND trim(scientific_name) != ''
        )
        SELECT
            scientific_name AS species,
            common_names,
            native_status,
            is_evergreen,
            mature_height_ft,
            canopy_spread_ft,
            growth_rate,
            lifespan_years,
            drought_tolerance,
            bloom_season,
            wildlife_value,
            fire_risk,
            tree_category,
            is_complete_flag AS is_complete,
            icon_rgba_b64,
            icon_width,
            icon_height,
            enriched_at
        FROM ranked
        WHERE rn = 1
        ORDER BY species
        """
    ).fetch_arrow_table()

    conn.close()

    after_count = len(result)
    deduped = before_count - after_count
    print(
        f"[info] output rows: {after_count} "
        f"({'no change' if deduped == 0 else f'{deduped} duplicates collapsed'})",
        file=sys.stderr,
    )

    if args.dry_run:
        print("[dry-run] skipping write and upload", file=sys.stderr)
        return

    pq.write_table(result, OUTPUT_PATH)
    print(f"[info] wrote {OUTPUT_PATH}", file=sys.stderr)

    r = subprocess.run(
        ["gsutil", "cp", OUTPUT_PATH, ENRICHMENT_GCS_URI],
        capture_output=True,
        text=True,
    )
    if r.returncode == 0:
        print(f"[info] uploaded to {ENRICHMENT_GCS_URI}", file=sys.stderr)
    else:
        print(f"[error] gsutil upload failed:\n{r.stderr.strip()}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
