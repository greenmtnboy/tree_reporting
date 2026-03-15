# Shared constants for tree_enrichment.py and tree_enrichment_probe.py.
# Both scripts import from here so exclusion logic stays in one place.

ENRICHMENT_PARQUET = "https://storage.googleapis.com/trilogy_public_models/duckdb/trees/tree_enrichment.parquet"
ENRICHMENT_GCS_URI = "gs://trilogy_public_models/duckdb/trees/tree_enrichment.parquet"
TREE_INFO_PARQUET  = "https://storage.googleapis.com/trilogy_public_models/duckdb/trees/full_tree_info.parquet"

# Species that are not real trees and should never be enriched.
# All values must be lowercase because should_skip_species() lowercases before comparing.
EXCLUDED_SPECIES: set[str] = {
    "::",
    "tree",
    "to be determine'd",
    ":: tree",
    ":: brisbane box",
    ":: to be determine",
}

# Species present in tree_info that represent vacant / placeholder records — skip enrichment.
SKIP_SPECIES: set[str] = {
    "Scheduled Planting Site - Spring 2026",
    "Vacant Unacceptable/Retired",
    "Vacant site medium",
}

# SQL fragment for use in WHERE clauses when selecting from tree_info parquet.
# Filters out all excluded species in one place.
SPECIES_EXCLUSION_SQL = (
    "species IS NOT NULL"
    " AND lower(trim(species)) NOT IN ("
    "  '::', 'tree', 'to be determine''d',"
    "  ':: tree', ':: brisbane box', ':: to be determine'"
    ")"
)


def should_skip_species(species: str) -> bool:
    return species.strip().lower() in EXCLUDED_SPECIES
