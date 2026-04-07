#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pillow", "instructor[litellm]", "duckdb", "google-genai", "jsonref", "pytrilogy", "pydantic", "google-cloud-storage"]
# ///

import sys
import os
import argparse
import json as _json
import time as _time
import urllib.request as _urllib_request
import urllib.parse as _urllib_parse
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
import requests
import duckdb
from datetime import datetime, timezone
from ecoregion_matcher import (
    EcoregionReference,
    build_native_range_evidence as build_native_range_evidence_for_entries,
    make_ecoregion_reference,
    select_ecoregion_candidates as select_ecoregion_candidates_from_evidence,
)
from _ecoregion_shared import (
    LAYER_QUERY_URL,
    REMOTE_ECOREGION_PARQUET,
)
from enrichment._tree_shared import (
    ENRICHMENT_PARQUET,
    ENRICHMENT_GCS_URI,
    TREE_INFO_PARQUET,
    SKIP_SPECIES,
    SPECIES_EXCLUSION_SQL,
    should_skip_species,
)
from enrichment._tree_enrichment_helpers import (
    build_inat_lookup_candidates,
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
from enrichment._tree_enrichment_llm import (
    DEFAULT_INSTRUCTOR_MODEL,
    DEFAULT_VERTEX_LOCATION,
    DEFAULT_VERTEX_PROJECT,
    create_instructor_client,
    debug_print_source_details,
    normalize_instructor_model_name,
    parse_enrichment_from_text_v2,
)
from enrichment._tree_enrichment_models import TreeEnrichment
from enrichment._tree_enrichment_sources import (
    SourceTexts,
    build_reference_text,
    gather_source_texts,
    source_labels,
)

# ── iNaturalist photo fetching ─────────────────────────────────────────────────

_INAT_BASE = "https://api.inaturalist.org/v1"
_INAT_ACCEPTABLE_LICENSES: frozenset[str] = frozenset({"cc0", "cc-by", "cc-by-sa", "cc-by-nc", "cc-by-nc-sa"})


def _inat_get(path: str, params: dict) -> dict:
    url = f"{_INAT_BASE}{path}?{_urllib_parse.urlencode(params)}"
    req = _urllib_request.Request(url, headers={"User-Agent": "tree-enrichment/1.0"})
    with _urllib_request.urlopen(req, timeout=15) as resp:
        return _json.loads(resp.read())


def _fetch_inat_photo_for_query(query_name: str) -> tuple[int | None, str | None, str | None, str | None]:
    data = _inat_get("/taxa", {"q": query_name, "rank": "species,hybrid", "per_page": 3})
    results = data.get("results", [])
    if not results:
        return None, None, None, None

    lower_name = query_name.lower().replace(" x ", " × ")
    taxon = None
    for r in results:
        r_name = r.get("name", "").lower()
        if r_name == lower_name or r_name == query_name.lower():
            taxon = r
            break
    if taxon is None:
        taxon = results[0]

    taxon_id: int = taxon["id"]

    dp = taxon.get("default_photo")
    if dp and dp.get("license_code") in _INAT_ACCEPTABLE_LICENSES:
        return taxon_id, dp.get("medium_url"), dp.get("license_code"), dp.get("attribution")

    license_param = ",".join(sorted(_INAT_ACCEPTABLE_LICENSES))
    obs_data = _inat_get("/observations", {
        "taxon_id": taxon_id,
        "photos": "true",
        "quality_grade": "research",
        "license": license_param,
        "photo_license": license_param,
        "per_page": 5,
        "order_by": "votes",
    })
    for obs in obs_data.get("results", []):
        for photo in obs.get("photos", []):
            lic = photo.get("license_code")
            if lic in _INAT_ACCEPTABLE_LICENSES:
                url = photo["url"].replace("square", "medium")
                return taxon_id, url, lic, photo.get("attribution")

    return taxon_id, None, None, None


def fetch_inat_photo(scientific_name: str) -> tuple[int | None, str | None, str | None, str | None]:
    """Return (taxon_id, photo_url, photo_license, photo_attribution).

    Tries the curated taxon default photo first; falls back to the best
    research-grade observation photo with an acceptable open license.
    Returns (None, None, None, None) on any network failure or when no
    acceptable-license photo exists.
    """
    try:
        candidates = build_inat_lookup_candidates(scientific_name)
        fallback_result: tuple[int | None, str | None, str | None, str | None] | None = None
        for idx, candidate in enumerate(candidates):
            taxon_id, photo_url, photo_license, photo_attribution = _fetch_inat_photo_for_query(candidate)
            if photo_url:
                if idx > 0:
                    print(
                        f"    [inat] fallback photo lookup {scientific_name!r} -> {candidate!r}",
                        file=sys.stderr,
                    )
                return taxon_id, photo_url, photo_license, photo_attribution
            if fallback_result is None and taxon_id is not None:
                fallback_result = (taxon_id, photo_url, photo_license, photo_attribution)

        if fallback_result is not None:
            return fallback_result
        return None, None, None, None

    except Exception as exc:
        print(f"    [inat] failed for {scientific_name!r}: {exc}", file=sys.stderr)
        return None, None, None, None


# ── External data sources ───────────────────────────────────────────────────────

_ECOREGION_CACHE: list[EcoregionReference] | None = None


def load_ecoregion_references() -> list[EcoregionReference]:
    global _ECOREGION_CACHE
    if _ECOREGION_CACHE is not None:
        return _ECOREGION_CACHE

    rows: list[tuple[int, str, str | None, str | None]] = []

    conn = duckdb.connect()
    try:
        try:
            rows = conn.execute(
                """
                SELECT
                    ecoregion_id,
                    ecoregion_name,
                    realm,
                    biome
                FROM read_parquet(?)
                WHERE ecoregion_id IS NOT NULL
                ORDER BY ecoregion_id
                """,
                [REMOTE_ECOREGION_PARQUET],
            ).fetchall()
        except Exception:
            rows = []
    finally:
        conn.close()

    if not rows:
        try:
            response = requests.get(
                LAYER_QUERY_URL,
                params={
                    "where": "ECO_ID IS NOT NULL AND ECO_ID > 0",
                    "outFields": "ECO_ID,ECO_NAME,REALM,BIOME_NAME",
                    "returnGeometry": "false",
                    "orderByFields": "ECO_ID",
                    "f": "json",
                },
                timeout=120,
            )
            response.raise_for_status()
            payload = response.json()
            rows = [
                (
                    int(feature["attributes"]["ECO_ID"]),
                    feature["attributes"]["ECO_NAME"],
                    feature["attributes"].get("REALM"),
                    feature["attributes"].get("BIOME_NAME"),
                )
                for feature in payload.get("features", [])
                if feature.get("attributes", {}).get("ECO_ID") is not None
            ]
        except Exception:
            rows = []

    _ECOREGION_CACHE = [
        make_ecoregion_reference(
            ecoregion_id=ecoregion_id,
            ecoregion_name=ecoregion_name,
            realm=realm,
            biome=biome,
        )
        for ecoregion_id, ecoregion_name, realm, biome in rows
    ]
    return _ECOREGION_CACHE


def build_native_range_evidence(texts: SourceTexts, reference_text: str) -> str:
    return build_native_range_evidence_for_entries(
        [
            ("Wikipedia", texts.wikipedia),
            ("POWO", texts.powo),
            ("GBIF", texts.gbif),
            ("SelecTree", texts.selectree),
        ],
        reference_text,
    )


def select_ecoregion_candidates(native_range_evidence: str, limit: int = 40) -> list[EcoregionReference]:
    return select_ecoregion_candidates_from_evidence(
        native_range_evidence,
        load_ecoregion_references(),
        limit=limit,
    )


def run_standalone_debug(q_species: str, client) -> int:
    scientific_name = parse_scientific_name(q_species)
    if not scientific_name:
        print("[debug] empty species", file=sys.stderr)
        return 2

    wiki_name = map_wikipedia_lookup(scientific_name)
    print(f"[debug] input: {q_species}", file=sys.stderr)
    print(f"[debug] scientific_name: {scientific_name}", file=sys.stderr)
    print(f"[debug] wikipedia_lookup: {wiki_name}", file=sys.stderr)

    texts = gather_source_texts(scientific_name, wiki_name)
    labels = source_labels(texts)
    native_range_evidence = build_native_range_evidence(texts, build_reference_text(texts))
    ecoregion_candidates = select_ecoregion_candidates(native_range_evidence)
    if labels:
        print(f"[debug] sources: {', '.join(labels)}", file=sys.stderr)
    else:
        print("[debug] sources: none", file=sys.stderr)
    print(f"[debug] ecoregion shortlist count: {len(ecoregion_candidates)}", file=sys.stderr)

    debug_print_source_details(texts)

    reference_text = build_reference_text(texts)
    print(f"[debug] combined_reference_chars: {len(reference_text)}", file=sys.stderr)

    enrichment = parse_enrichment_from_text_v2(
        scientific_name,
        wiki_name,
        texts,
        client,
        native_range_evidence,
        ecoregion_candidates,
        print_full_context=True,
    )
    if enrichment is None:
        print("[debug] enrichment: none", file=sys.stderr)
        return 1

    is_complete, missing = compute_is_complete(enrichment)
    print("[debug] parsed enrichment JSON:", file=sys.stderr)
    print(enrichment.model_dump_json(indent=2), file=sys.stderr)
    if is_complete:
        print("[debug] completeness: complete", file=sys.stderr)
    else:
        print(f"[debug] completeness: incomplete; missing: {', '.join(missing)}", file=sys.stderr)
    return 0


# ── Enrichment ─────────────────────────────────────────────────────────────────

def enrich_species(q_species: str, client, print_full_context: bool = False) -> TreeEnrichment | None:
    scientific_name = parse_scientific_name(q_species)
    if not scientific_name:
        return None
    
    wiki_name = map_wikipedia_lookup(scientific_name)

    # Gather text from all available sources
    texts = gather_source_texts(scientific_name, wiki_name)
    labels = source_labels(texts)

    if not labels:
        print(
            f"  [skip] no content found for {scientific_name!r} (lookup: {wiki_name!r})",
            file=sys.stderr,
        )
        return None

    print(f"    [sources] {', '.join(labels)}", file=sys.stderr)
    native_range_evidence = build_native_range_evidence(texts, build_reference_text(texts))
    ecoregion_candidates = select_ecoregion_candidates(native_range_evidence)
    if ecoregion_candidates:
        print(f"    [ecoregions] shortlist={len(ecoregion_candidates)}", file=sys.stderr)
    return parse_enrichment_from_text_v2(
        scientific_name,
        wiki_name,
        texts,
        client,
        native_range_evidence,
        ecoregion_candidates,
        print_full_context=print_full_context,
    )


# ── Species list ───────────────────────────────────────────────────────────────

def get_all_species() -> list[str]:
    """Return all distinct species values from the tree dataset."""
    conn = duckdb.connect()
    try:
        rows = conn.execute(
            f"""
            SELECT DISTINCT species
            FROM read_parquet(?)
            WHERE {SPECIES_EXCLUSION_SQL}
            ORDER BY species
            """,
            [TREE_INFO_PARQUET],
        ).fetchall()
        return [row[0] for row in rows if not should_skip_species(row[0])]
    finally:
        conn.close()


_NEW_COMPLETENESS_EXPR = """
    (common_names IS NOT NULL
     AND array_length(common_names) > 0
     AND is_evergreen IS NOT NULL
     AND mature_height_max_ft IS NOT NULL
     AND canopy_spread_max_ft IS NOT NULL
     AND growth_rate IS NOT NULL
     AND drought_tolerance IS NOT NULL
     AND tree_form IS NOT NULL)
""".strip()

def parquet_exists(source: str) -> bool:
    """Return True if *source* (local path or https URL) points to an existing parquet file."""
    if source.startswith("http://") or source.startswith("https://"):
        resp = requests.head(source, timeout=10)
        return resp.status_code == 200
    return os.path.exists(source)


def get_already_enriched(source: str = ENRICHMENT_PARQUET) -> set[str]:
    """Return the set of species names already present in *source* (local path or https URL).

    Returns an empty set only if the file does not exist. Raises on any other error.
    """
    if not parquet_exists(source):
        return set(SKIP_SPECIES)
    conn = duckdb.connect()
    try:
        rows = conn.execute(
            """
            SELECT species
            FROM read_parquet(?)
            WHERE species IS NOT NULL
            """,
            [source],
        ).fetchall()
        return {row[0] for row in rows}.union(SKIP_SPECIES)
    finally:
        conn.close()


def _pa_type_to_duckdb_sql(t: pa.DataType) -> str:
    """Convert a PyArrow type to the equivalent DuckDB SQL type string."""
    if pa.types.is_boolean(t):
        return "BOOLEAN"
    if pa.types.is_int32(t):
        return "INTEGER"
    if pa.types.is_int64(t):
        return "BIGINT"
    if pa.types.is_float32(t) or pa.types.is_float16(t):
        return "FLOAT"
    if pa.types.is_float64(t):
        return "DOUBLE"
    if pa.types.is_string(t) or pa.types.is_large_string(t):
        return "VARCHAR"
    if pa.types.is_timestamp(t):
        return "TIMESTAMPTZ" if t.tz else "TIMESTAMP"
    if pa.types.is_date(t):
        return "DATE"
    if pa.types.is_list(t):
        inner = _pa_type_to_duckdb_sql(t.value_type)
        return f"{inner}[]"
    return "VARCHAR"


def load_existing_table(source: str) -> pa.Table | None:
    """Load the full enrichment parquet from *source*.

    Returns None only if the file does not exist. Raises on any other error.
    Column patching is schema-driven: any column in SCHEMA missing from the
    source parquet is synthesised as ``CAST(NULL AS <type>)`` so the query
    never fails when the parquet predates a schema addition.
    """
    if not parquet_exists(source):
        return None
    conn = duckdb.connect()
    try:
        try:
            col_rows = conn.execute(
                "SELECT column_name FROM (DESCRIBE SELECT * FROM read_parquet(?)) LIMIT 200",
                [source],
            ).fetchall()
            existing_cols = {row[0] for row in col_rows}
        except Exception:
            existing_cols = set()

        schema_field_map = {f.name: f.type for f in SCHEMA}

        def _col(name: str) -> str:
            if name in existing_cols:
                return name
            sql_type = _pa_type_to_duckdb_sql(schema_field_map[name])
            return f"CAST(NULL AS {sql_type}) AS {name}"

        # Build column list from SCHEMA — is_complete is always recomputed
        data_cols = ", ".join(
            _col(f.name) for f in SCHEMA
            if f.name not in ("is_complete", "enriched_at")
        )
        table = conn.execute(
            f"""
            SELECT
              {data_cols},
              {_NEW_COMPLETENESS_EXPR} AS is_complete,
              {_col("enriched_at")}
            FROM read_parquet(?)
            """,
            [source],
        ).fetch_arrow_table()
    except Exception as exc:
        raise RuntimeError(f"load_existing_table({source!r}) failed: {exc}") from exc
    finally:
        conn.close()

    # Drop any extra columns not in SCHEMA (e.g. legacy icon_rgba_b64/width/height)
    schema_names = {f.name for f in SCHEMA}
    extra = [name for name in table.schema.names if name not in schema_names]
    if extra:
        table = table.drop(extra)

    # Cast to canonical SCHEMA — normalizes list child field names (DuckDB emits 'l', PyArrow uses 'item')
    # and ensures all column types match exactly before concat.
    table = table.cast(SCHEMA)

    return table


def merge_with_existing(existing: pa.Table | None, new_rows: list[dict]) -> pa.Table:
    """Merge *new_rows* into *existing*, adding only new species (no replacements expected)."""
    new_table = build_table(new_rows)
    existing_count = len(existing) if existing is not None else 0
    print(
        f"[merge] existing={existing_count} | new={len(new_rows)} | expected_total={existing_count + len(new_rows)}",
        file=sys.stderr,
    )
    if existing is None or existing_count == 0:
        return new_table
    re_processed = pa.array([row["species"] for row in new_rows], type=pa.string())
    overlap = pc.sum(pc.is_in(existing.column("species"), re_processed)).as_py() or 0
    if overlap > 0:
        raise RuntimeError(
            f"merge_with_existing: {overlap} species in new_rows already exist in existing table — "
            f"re-processing is not expected. Aborting to avoid data loss."
        )
    merged = pa.concat_tables([existing, new_table])
    print(f"[merge] result={len(merged)} rows", file=sys.stderr)
    if len(merged) != existing_count + len(new_rows):
        raise RuntimeError(
            f"merge_with_existing: expected {existing_count + len(new_rows)} rows after concat, got {len(merged)}"
        )
    return merged


def upload_to_gcs(local_path: str, gcs_uri: str) -> None:
    """Upload *local_path* to *gcs_uri* using the google-cloud-storage Python library."""
    from google.cloud import storage as gcs

    print(f"[info] uploading {local_path} → {gcs_uri}", file=sys.stderr)
    # Parse gs://bucket/path
    if not gcs_uri.startswith("gs://"):
        raise ValueError(f"expected gs:// URI, got {gcs_uri!r}")
    without_scheme = gcs_uri[len("gs://"):]
    bucket_name, _, blob_name = without_scheme.partition("/")

    client = gcs.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    blob.upload_from_filename(local_path)
    print("[info] upload complete", file=sys.stderr)


# ── Arrow table ────────────────────────────────────────────────────────────────

SCHEMA = pa.schema([
    ("species",               pa.string()),
    ("genus",                 pa.string()),
    ("species_epithet",       pa.string()),
    ("family",                pa.string()),
    ("common_names",          pa.list_(pa.string())),
    ("description",           pa.string()),
    ("is_evergreen",          pa.bool_()),
    ("mature_height_min_ft",  pa.float32()),
    ("mature_height_max_ft",  pa.float32()),
    ("canopy_spread_min_ft",  pa.float32()),
    ("canopy_spread_max_ft",  pa.float32()),
    ("growth_rate",           pa.string()),
    ("lifespan_min_years",    pa.int32()),
    ("lifespan_max_years",    pa.int32()),
    ("drought_tolerance",     pa.string()),
    ("water_needs",           pa.string()),
    ("sun_exposure",          pa.list_(pa.string())),
    ("soil_preferences",      pa.list_(pa.string())),
    ("root_behavior",         pa.string()),
    ("coastal_tolerance",     pa.bool_()),
    ("salt_tolerance",        pa.bool_()),
    ("pollution_tolerance",   pa.string()),
    ("bloom_months",          pa.list_(pa.int32())),
    ("wildlife_value",        pa.string()),
    ("fire_risk",             pa.string()),
    ("tree_form",             pa.string()),
    ("usda_zone_min",         pa.int32()),
    ("usda_zone_max",         pa.int32()),
    ("native_ecoregions",     pa.list_(pa.int32())),
    ("inat_taxon_id",         pa.int64()),
    ("photo_url",             pa.string()),
    ("photo_license",         pa.string()),
    ("photo_attribution",     pa.string()),
    ("is_complete",           pa.bool_()),
    ("enriched_at",           pa.timestamp("us", tz="UTC")),
])


def build_table(rows: list[dict]) -> pa.Table:
    return pa.Table.from_pylist(rows, schema=SCHEMA)


def emit(table: pa.Table) -> None:
    with pa.ipc.new_stream(sys.stdout.buffer, table.schema) as writer:
        writer.write_table(table)


# ── Main ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--debug-species",
        dest="debug_species",
        help="Run standalone parsing debug for a single species, e.g. \"Abutilon hybridum\"",
    )
    parser.add_argument(
        "--print-llm-context",
        action="store_true",
        help="Print full concatenated source text sent to the LLM for each processed species.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help=(
            "Process at most N species. Writes checkpoints to --output and uploads "
            "to GCS at the end. Reads progress from the local file instead of remote."
        ),
    )
    parser.add_argument(
        "--output",
        default="tree_enrichment.parquet",
        metavar="PATH",
        help="Local parquet checkpoint file used with --limit (default: tree_enrichment.parquet).",
    )
    parser.add_argument(
        "--flush-every",
        dest="flush_every",
        type=int,
        default=10,
        metavar="N",
        help="Write a checkpoint to --output every N successfully enriched species (default: 10).",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_INSTRUCTOR_MODEL,
        help=(
            "Instructor model to use for enrichment. Accepts either a full provider "
            f"model name or a Gemini shorthand (default: {DEFAULT_INSTRUCTOR_MODEL})."
        ),
    )
    parser.add_argument(
        "--vertex-project",
        dest="vertex_project",
        default=DEFAULT_VERTEX_PROJECT,
        help=f"Vertex AI project for instructor provider calls (default: {DEFAULT_VERTEX_PROJECT}).",
    )
    parser.add_argument(
        "--vertex-location",
        dest="vertex_location",
        default=DEFAULT_VERTEX_LOCATION,
        help=f"Vertex AI location for instructor provider calls (default: {DEFAULT_VERTEX_LOCATION}).",
    )
    args = parser.parse_args()

    normalized_model = normalize_instructor_model_name(args.model)
    print(
        f"[info] instructor model={normalized_model} | "
        f"vertex_project={args.vertex_project} | vertex_location={args.vertex_location}",
        file=sys.stderr,
    )
    client = create_instructor_client(
        normalized_model,
        args.vertex_project,
        args.vertex_location,
    )

    if args.debug_species:
        raise SystemExit(run_standalone_debug(args.debug_species, client))

    # In local-refresh mode, read progress from the local checkpoint if it exists;
    # otherwise fall back to the remote parquet.
    local_mode = args.limit is not None
    if local_mode and os.path.exists(args.output):
        enrichment_source = args.output
        print(f"[info] local mode: reading progress from {args.output}", file=sys.stderr)
    else:
        enrichment_source = ENRICHMENT_PARQUET

    already_enriched = get_already_enriched(enrichment_source)
    all_species = get_all_species()
    to_process = [s for s in all_species if parse_scientific_name(s) not in already_enriched]
    if local_mode:
        to_process = to_process[:args.limit]

    print(
        f"[info] {len(all_species)} total species | "
        f"{len(already_enriched)} already enriched | "
        f"{len(to_process)} to process",
        file=sys.stderr,
    )

    # Pre-load existing table once so every checkpoint flush is cheap.
    existing_table = load_existing_table(enrichment_source) if already_enriched else None
    if already_enriched and (existing_table is None or len(existing_table) == 0):
        raise RuntimeError(
            f"existing_table is empty/None but get_already_enriched() returned "
            f"{len(already_enriched)} species from {enrichment_source!r}. "
            f"load_existing_table() silently failed — check schema compatibility or URL reachability."
        )
    
    new_rows: list[dict] = []
    counter = 0
    for q_species in to_process:
        if not q_species:
            continue
        status = "re-enrich" if parse_scientific_name(q_species) in already_enriched else "new"
        print(f"{counter:03d}/{len(to_process):03d}  [{status}] {q_species}", file=sys.stderr)
        enrichment = enrich_species(q_species, client, print_full_context=args.print_llm_context)
        if enrichment is None:
            continue
        counter += 1
        if counter>250:
            break
        is_complete, missing = compute_is_complete(enrichment)
        if missing:
            print(f"    [incomplete] missing: {', '.join(missing)}", file=sys.stderr)
        else:
            print(f"    [complete]", file=sys.stderr)
        scientific_name = parse_scientific_name(q_species)
        genus, species_epithet = split_scientific_parts(scientific_name)
        lifespan_min_years, lifespan_max_years = parse_lifespan_range(enrichment.lifespan_years)
        mature_height_min_ft, mature_height_max_ft = convert_length_range_to_feet(
            enrichment.mature_height_min_value,
            enrichment.mature_height_max_value,
            enrichment.mature_height_unit,
        )
        canopy_spread_min_ft, canopy_spread_max_ft = convert_length_range_to_feet(
            enrichment.canopy_spread_min_value,
            enrichment.canopy_spread_max_value,
            enrichment.canopy_spread_unit,
        )
        bloom_months = normalize_bloom_months(enrichment.bloom_months)
        normalized_growth_rate = normalize_growth_rate(
            enrichment.growth_rate_min_value,
            enrichment.growth_rate_max_value,
            enrichment.growth_rate_unit,
            enrichment.growth_rate,
        )
        inat_taxon_id, photo_url, photo_license, photo_attribution = fetch_inat_photo(scientific_name)
        _time.sleep(0.3)  # stay well under iNat 100 req/min limit
        new_rows.append({
            "species":              scientific_name,
            "genus":                genus,
            "species_epithet":      species_epithet,
            "family":               None,
            "common_names":         enrichment.common_names or None,
            "description":          enrichment.description.strip() if enrichment.description else None,
            "is_evergreen":         enrichment.is_evergreen,
            "mature_height_min_ft": mature_height_min_ft,
            "mature_height_max_ft": mature_height_max_ft,
            "canopy_spread_min_ft": canopy_spread_min_ft,
            "canopy_spread_max_ft": canopy_spread_max_ft,
            "growth_rate":          normalized_growth_rate,
            "lifespan_min_years":   lifespan_min_years,
            "lifespan_max_years":   lifespan_max_years,
            "drought_tolerance":    enrichment.drought_tolerance,
            "water_needs":          enrichment.water_needs,
            "sun_exposure":         enrichment.sun_exposure or None,
            "soil_preferences":     enrichment.soil_preferences or None,
            "root_behavior":        enrichment.root_behavior,
            "coastal_tolerance":    enrichment.coastal_tolerance,
            "salt_tolerance":       enrichment.salt_tolerance,
            "pollution_tolerance":  enrichment.pollution_tolerance,
            "bloom_months":         bloom_months,
            "wildlife_value":       enrichment.wildlife_value,
            "fire_risk":            enrichment.fire_risk,
            "tree_form":            map_tree_form(enrichment.tree_form),
            "usda_zone_min":        enrichment.usda_zone_min,
            "usda_zone_max":        enrichment.usda_zone_max,
            "native_ecoregions":    sorted(set(enrichment.native_ecoregions)) or None,
            "inat_taxon_id":        inat_taxon_id,
            "photo_url":            photo_url,
            "photo_license":        photo_license,
            "photo_attribution":    photo_attribution,
            "is_complete":          is_complete,
            "enriched_at":          datetime.now(tz=timezone.utc),
        })

        # Periodic checkpoint flush
        if local_mode and len(new_rows) % args.flush_every == 0:
            checkpoint = merge_with_existing(existing_table, new_rows)
            pq.write_table(checkpoint, args.output)
            print(
                f"  [checkpoint] {len(new_rows)} new rows written to {args.output}",
                file=sys.stderr,
            )
    
    merged = merge_with_existing(existing_table, new_rows)

    if existing_table is not None and len(existing_table) > 0 and new_rows:
        new_species_set = {row["species"] for row in new_rows}
        overlap = sum(1 for s in existing_table.column("species").to_pylist() if s in new_species_set)
        expected = len(existing_table) - overlap + len(new_rows)
        if len(merged) != expected:
            raise RuntimeError(
                f"Merge validation failed: expected {expected} rows "
                f"({len(existing_table)} existing − {overlap} re-processed + {len(new_rows)} new), "
                f"got {len(merged)}. "
                f"Existing rows were likely dropped due to a silent load failure."
            )

    if local_mode:
        pq.write_table(merged, args.output)
        print(f"[info] wrote {len(merged)} rows to {args.output}", file=sys.stderr)
        upload_to_gcs(args.output, ENRICHMENT_GCS_URI)
    else:
        emit(merged)
