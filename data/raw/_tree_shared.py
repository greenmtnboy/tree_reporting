# Shared constants for tree_enrichment.py and tree_enrichment_probe.py.
# Both scripts import from here so exclusion logic stays in one place.

from trilogy import Environment
from pathlib import Path

env = Environment(working_path =Path(__file__).parent)

env.parse('''import core;''')

# get this out of the trilogy file
DATA_VERSION = env.concepts['local.data_version'].lineage.arguments[0]

ENRICHMENT_PARQUET = f"https://storage.googleapis.com/trilogy_public_models/duckdb/trees/tree_enrichment_v{DATA_VERSION}.parquet?cb={__import__('random').randint(0, 2**32)}"
ENRICHMENT_GCS_URI = f"gs://trilogy_public_models/duckdb/trees/tree_enrichment_v{DATA_VERSION}.parquet"
TREE_INFO_PARQUET  = f"https://storage.googleapis.com/trilogy_public_models/duckdb/trees/full_tree_info_v{DATA_VERSION}.parquet"

# Species that are not real trees and should never be enriched.
# All values must be lowercase because should_skip_species() lowercases before comparing.
# After the scientific-name refactor, malformed SF entries that began with "::" reduce to
# an empty string once the prefix is stripped — so "" is also excluded.
EXCLUDED_SPECIES: set[str] = {
    "",
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
    " AND trim(species) != ''"
    " AND lower(trim(species)) NOT IN ("
    "  '::', 'tree', 'to be determine''d',"
    "  ':: tree', ':: brisbane box', ':: to be determine'"
    ")"
)


def should_skip_species(species: str) -> bool:
    return species.strip().lower() in EXCLUDED_SPECIES
