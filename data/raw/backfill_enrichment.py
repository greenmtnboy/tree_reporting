#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "instructor[litellm]", "duckdb", "google-genai", "jsonref", "pytrilogy", "pydantic", "google-cloud-storage"]
# ///
"""
Backfill NULL fields in the enrichment parquet without re-running full enrichment.

Each *provider* knows how to fill a specific set of fields. Only the providers
needed for the requested --fields are invoked, and only for rows where those
fields are currently NULL. Existing non-null values are never overwritten.

Providers
---------
  inat_photos — fills: inat_taxon_id, photo_url, photo_license, photo_attribution
                source: iNaturalist API (no credentials needed)

  llm         — fills: all LLM-derived fields (description, common_names, tree_form, …)
                source: Instructor LLM (requires --model, optionally --vertex-*)

Usage
-----
  # Backfill iNat photo columns (no LLM needed)
  uv run backfill_enrichment.py --fields photo_url

  # Preview which species need backfill without running
  uv run backfill_enrichment.py --fields photo_url --dry-run

  # Backfill description for species missing it
  uv run backfill_enrichment.py --fields description --model gemini-2.0-flash

  # Multiple fields — all providers needed are resolved automatically
  uv run backfill_enrichment.py --fields photo_url,description --model gemini-2.0-flash

  # Cap how many species to process, write a local checkpoint
  uv run backfill_enrichment.py --fields photo_url --limit 50 --output tree_enrichment.parquet

  # Use a local checkpoint as the source instead of the remote GCS parquet
  uv run backfill_enrichment.py --fields photo_url --source tree_enrichment.parquet --output tree_enrichment.parquet
"""

import sys
import os
import argparse
import time as _time
import pyarrow as pa
import pyarrow.parquet as pq
import duckdb

from enrichment._tree_shared import (
    ENRICHMENT_PARQUET,
    ENRICHMENT_GCS_URI,
    should_skip_species,
)

# Local filename derived from the GCS URI, e.g. "tree_enrichment_v2.parquet"
_DEFAULT_OUTPUT = os.path.basename(ENRICHMENT_GCS_URI)
from enrichment._tree_enrichment_helpers import (
    compute_is_complete,
    convert_length_range_to_feet,
    map_tree_form,
    map_wikipedia_lookup,
    normalize_bloom_months,
    normalize_growth_rate,
    parse_lifespan_range,
    parse_scientific_name,
    split_scientific_parts,
)

# Import the pieces we reuse from tree_enrichment without re-running its __main__
import importlib.util as _ilu, pathlib as _pl
_te_spec = _ilu.spec_from_file_location("tree_enrichment", _pl.Path(__file__).parent / "tree_enrichment.py")
_te = _ilu.module_from_spec(_te_spec)  # type: ignore[arg-type]
_te_spec.loader.exec_module(_te)  # type: ignore[union-attr]

SCHEMA: pa.Schema = _te.SCHEMA
upload_to_gcs = _te.upload_to_gcs
parquet_exists = _te.parquet_exists
fetch_inat_photo = _te.fetch_inat_photo
enrich_species = _te.enrich_species


# ── Provider registry ──────────────────────────────────────────────────────────

# Maps each provider name to the complete set of fields it can fill.
PROVIDER_FIELDS: dict[str, frozenset[str]] = {
    "inat_photos": frozenset({
        "inat_taxon_id", "photo_url", "photo_license", "photo_attribution",
    }),
    "llm": frozenset({
        "common_names", "description", "is_evergreen",
        "mature_height_min_ft", "mature_height_max_ft",
        "canopy_spread_min_ft", "canopy_spread_max_ft",
        "growth_rate", "lifespan_min_years", "lifespan_max_years",
        "drought_tolerance", "water_needs", "sun_exposure",
        "soil_preferences", "root_behavior", "coastal_tolerance",
        "salt_tolerance", "pollution_tolerance",
        "bloom_months", "wildlife_value", "fire_risk", "tree_form",
        "usda_zone_min", "usda_zone_max", "native_ecoregions",
    }),
}

# Reverse map: field → provider name
FIELD_TO_PROVIDER: dict[str, str] = {
    field: provider
    for provider, fields in PROVIDER_FIELDS.items()
    for field in fields
}

ALL_FILLABLE_FIELDS: frozenset[str] = frozenset().union(*PROVIDER_FIELDS.values())


# ── Table loading ──────────────────────────────────────────────────────────────

_pa_type_to_duckdb_sql = _te._pa_type_to_duckdb_sql


def load_full_table(source: str) -> pa.Table:
    """Load existing parquet, patching missing columns with NULLs to match SCHEMA."""
    conn = duckdb.connect()
    try:
        col_rows = conn.execute(
            "SELECT column_name FROM (DESCRIBE SELECT * FROM read_parquet(?)) LIMIT 200",
            [source],
        ).fetchall()
        existing_cols = {row[0] for row in col_rows}
    except Exception:
        existing_cols = set()
    finally:
        conn.close()

    # Derive DuckDB NULL type from SCHEMA — no hardcoded type map needed
    schema_field_map = {f.name: f.type for f in SCHEMA}

    def _col(name: str) -> str:
        if name in existing_cols:
            return name
        sql_type = _pa_type_to_duckdb_sql(schema_field_map[name])
        return f"CAST(NULL AS {sql_type}) AS {name}"

    col_list = ", ".join(_col(f.name) for f in SCHEMA if f.name not in ("is_complete", "enriched_at"))
    enriched_at_sql = _col("enriched_at")

    conn = duckdb.connect()
    try:
        table = conn.execute(
            f"SELECT {col_list}, {enriched_at_sql} FROM read_parquet(?)",
            [source],
        ).fetch_arrow_table()
    finally:
        conn.close()

    # Cast to canonical schema (normalises list child names etc.)
    schema_no_is_complete = pa.schema([f for f in SCHEMA if f.name != "is_complete"])
    table = table.cast(schema_no_is_complete)
    return table


# ── Providers ──────────────────────────────────────────────────────────────────

def run_inat_provider(species: str) -> dict:
    """Call iNaturalist and return a partial row dict for the 4 iNat fields."""
    taxon_id, photo_url, photo_license, photo_attribution = fetch_inat_photo(species)
    _time.sleep(0.3)  # stay under iNat 100 req/min
    return {
        "inat_taxon_id":    taxon_id,
        "photo_url":        photo_url,
        "photo_license":    photo_license,
        "photo_attribution": photo_attribution,
    }


def run_llm_provider(species: str, client) -> dict:
    """Run LLM enrichment and return a partial row dict for all LLM-derived fields."""
    enrichment = enrich_species(species, client)
    if enrichment is None:
        return {}

    genus, species_epithet = split_scientific_parts(species)
    lifespan_min, lifespan_max = parse_lifespan_range(enrichment.lifespan_years)
    height_min, height_max = convert_length_range_to_feet(
        enrichment.mature_height_min_value,
        enrichment.mature_height_max_value,
        enrichment.mature_height_unit,
    )
    spread_min, spread_max = convert_length_range_to_feet(
        enrichment.canopy_spread_min_value,
        enrichment.canopy_spread_max_value,
        enrichment.canopy_spread_unit,
    )

    return {
        "genus":               genus,
        "species_epithet":     species_epithet,
        "common_names":        enrichment.common_names or None,
        "description":         enrichment.description.strip() if enrichment.description else None,
        "is_evergreen":        enrichment.is_evergreen,
        "mature_height_min_ft": height_min,
        "mature_height_max_ft": height_max,
        "canopy_spread_min_ft": spread_min,
        "canopy_spread_max_ft": spread_max,
        "growth_rate":         normalize_growth_rate(
            enrichment.growth_rate_min_value,
            enrichment.growth_rate_max_value,
            enrichment.growth_rate_unit,
            enrichment.growth_rate,
        ),
        "lifespan_min_years":  lifespan_min,
        "lifespan_max_years":  lifespan_max,
        "drought_tolerance":   enrichment.drought_tolerance,
        "water_needs":         enrichment.water_needs,
        "sun_exposure":        enrichment.sun_exposure or None,
        "soil_preferences":    enrichment.soil_preferences or None,
        "root_behavior":       enrichment.root_behavior,
        "coastal_tolerance":   enrichment.coastal_tolerance,
        "salt_tolerance":      enrichment.salt_tolerance,
        "pollution_tolerance": enrichment.pollution_tolerance,
        "bloom_months":        normalize_bloom_months(enrichment.bloom_months),
        "wildlife_value":      enrichment.wildlife_value,
        "fire_risk":           enrichment.fire_risk,
        "tree_form":           map_tree_form(enrichment.tree_form),
        "usda_zone_min":       enrichment.usda_zone_min,
        "usda_zone_max":       enrichment.usda_zone_max,
        "native_ecoregions":   sorted(set(enrichment.native_ecoregions)) or None,
    }


# ── Row-level merge ────────────────────────────────────────────────────────────

def apply_provider_updates(row: dict, updates: dict) -> tuple[dict, list[str]]:
    """
    Merge *updates* into *row*, touching only fields that are currently NULL.
    Returns (updated_row, list_of_fields_changed).
    """
    result = dict(row)
    changed = []
    for field, value in updates.items():
        if value is not None and result.get(field) is None:
            result[field] = value
            changed.append(field)
    return result, changed


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill NULL enrichment fields for existing species.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--fields",
        required=True,
        help="Comma-separated field names to backfill, e.g. 'photo_url' or 'photo_url,description'.",
    )
    parser.add_argument(
        "--source",
        default=ENRICHMENT_PARQUET,
        metavar="PATH_OR_URL",
        help=f"Parquet to read from (default: remote GCS). Use a local path for checkpoint-based runs.",
    )
    parser.add_argument(
        "--output",
        default=_DEFAULT_OUTPUT,
        metavar="PATH",
        help=f"Local parquet to write the updated table to (default: {_DEFAULT_OUTPUT}, matching the GCS filename).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Process at most N species.",
    )
    parser.add_argument(
        "--flush-every",
        dest="flush_every",
        type=int,
        default=10,
        metavar="N",
        help="Checkpoint to --output every N species (default: 10).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List which species need backfill without making any API calls or writes.",
    )
    parser.add_argument(
        "--upload",
        action="store_true",
        default=True,
        help="Upload to GCS after writing (default: true).",
    )
    parser.add_argument(
        "--no-upload",
        dest="upload",
        action="store_false",
        help="Skip GCS upload (write local file only).",
    )
    # LLM options — only needed if 'llm' provider is required
    parser.add_argument("--model", default=None)
    parser.add_argument("--vertex-project", dest="vertex_project", default=None)
    parser.add_argument("--vertex-location", dest="vertex_location", default=None)
    args = parser.parse_args()

    # Resolve requested fields
    target_fields = [f.strip() for f in args.fields.split(",") if f.strip()]
    unknown = [f for f in target_fields if f not in ALL_FILLABLE_FIELDS]
    if unknown:
        print(f"[error] unknown fields: {', '.join(unknown)}", file=sys.stderr)
        print(f"[info]  fillable fields: {', '.join(sorted(ALL_FILLABLE_FIELDS))}", file=sys.stderr)
        raise SystemExit(1)

    # Resolve which providers are needed
    providers_needed: set[str] = {FIELD_TO_PROVIDER[f] for f in target_fields}
    print(f"[info] target fields : {', '.join(target_fields)}", file=sys.stderr)
    print(f"[info] providers     : {', '.join(sorted(providers_needed))}", file=sys.stderr)

    # Validate LLM args if needed
    llm_client = None
    if "llm" in providers_needed:
        if not args.model:
            print("[error] --model is required when backfilling LLM fields", file=sys.stderr)
            raise SystemExit(1)
        from enrichment._tree_enrichment_llm import (
            create_instructor_client,
            normalize_instructor_model_name,
            DEFAULT_VERTEX_PROJECT,
            DEFAULT_VERTEX_LOCATION,
        )
        model = normalize_instructor_model_name(args.model)
        project = args.vertex_project or DEFAULT_VERTEX_PROJECT
        location = args.vertex_location or DEFAULT_VERTEX_LOCATION
        print(f"[info] llm model={model}", file=sys.stderr)
        llm_client = create_instructor_client(model, project, location)

    # Load existing table
    source = args.source
    if not parquet_exists(source):
        print(f"[error] source parquet not found: {source}", file=sys.stderr)
        raise SystemExit(1)

    print(f"[info] loading {source}", file=sys.stderr)
    table = load_full_table(source)
    rows = table.to_pylist()
    print(f"[info] loaded {len(rows)} rows", file=sys.stderr)

    # Find rows that need backfill (any target field is NULL)
    def needs_backfill(row: dict) -> bool:
        return any(row.get(f) is None for f in target_fields)

    to_backfill = [row for row in rows if not should_skip_species(row.get("species", "")) and needs_backfill(row)]
    if args.limit:
        to_backfill = to_backfill[:args.limit]

    print(
        f"[info] {len(to_backfill)} / {len(rows)} species need backfill",
        file=sys.stderr,
    )

    if args.dry_run:
        print(f"\n{'-'*60}", file=sys.stderr)
        print(f"DRY RUN — species that would be backfilled ({len(to_backfill)}):", file=sys.stderr)
        for row in to_backfill[:50]:
            null_fields = [f for f in target_fields if row.get(f) is None]
            print(f"  {row['species']}  (null: {', '.join(null_fields)})", file=sys.stderr)
        if len(to_backfill) > 50:
            print(f"  … and {len(to_backfill) - 50} more", file=sys.stderr)
        return

    if not to_backfill:
        print("[info] nothing to backfill — all target fields are already populated", file=sys.stderr)
        return

    # Build a mutable index of all rows by species for fast lookup during merge
    rows_by_species: dict[str, dict] = {row["species"]: row for row in rows}
    update_count = 0

    for i, row in enumerate(to_backfill):
        species = row["species"]
        pct = f"{i + 1:03d}/{len(to_backfill):03d}"
        print(f"{pct}  {species}", file=sys.stderr)

        merged_updates: dict = {}

        if "inat_photos" in providers_needed:
            updates = run_inat_provider(species)
            merged_updates.update(updates)

        if "llm" in providers_needed and llm_client is not None:
            updates = run_llm_provider(species, llm_client)
            merged_updates.update(updates)

        updated_row, changed = apply_provider_updates(rows_by_species[species], merged_updates)
        if changed:
            rows_by_species[species] = updated_row
            update_count += 1
            print(f"    [updated] {', '.join(changed)}", file=sys.stderr)
        else:
            print(f"    [no change]", file=sys.stderr)

        # Periodic checkpoint
        if args.flush_every and (i + 1) % args.flush_every == 0:
            _write_table(list(rows_by_species.values()), args.output)
            print(f"  [checkpoint] {i + 1} processed, {update_count} updated → {args.output}", file=sys.stderr)

    # Final write
    _write_table(list(rows_by_species.values()), args.output)
    print(f"\n[info] wrote {len(rows_by_species)} rows ({update_count} updated) → {args.output}", file=sys.stderr)

    if args.upload:
        upload_to_gcs(args.output, ENRICHMENT_GCS_URI)


def _write_table(rows: list[dict], path: str) -> None:
    """Rebuild is_complete and write the table to *path*."""
    from enrichment._tree_enrichment_models import TreeEnrichment
    import datetime

    schema_names = {f.name for f in SCHEMA}

    cleaned: list[dict] = []
    for row in rows:
        r = {k: v for k, v in row.items() if k in schema_names}
        # Recompute is_complete from current field population
        r["is_complete"] = _recompute_is_complete(r)
        # Ensure enriched_at is set
        if r.get("enriched_at") is None:
            r["enriched_at"] = datetime.datetime.now(tz=datetime.timezone.utc)
        cleaned.append(r)

    table = pa.Table.from_pylist(cleaned, schema=SCHEMA)
    pq.write_table(table, path)


def _recompute_is_complete(row: dict) -> bool:
    """Mirror the SQL completeness expression from tree_enrichment.py."""
    return bool(
        row.get("common_names")
        and row.get("is_evergreen") is not None
        and row.get("mature_height_max_ft") is not None
        and row.get("canopy_spread_max_ft") is not None
        and row.get("growth_rate") is not None
        and row.get("drought_tolerance") is not None
        and row.get("tree_form") is not None
    )


if __name__ == "__main__":
    main()
