# Shared constants for tree_enrichment.py and tree_enrichment_probe.py.
# Both scripts import from here so exclusion logic stays in one place.

import sys
from trilogy import Environment
from pathlib import Path
from random import randint

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _ingest_shared import SPECIES_SENTINELS  # noqa: E402

env = Environment(working_path=Path(__file__).resolve().parent.parent)

env.parse('''import core;''')

# get this out of the trilogy file
DATA_VERSION = env.concepts['local.data_version'].lineage.arguments[0]

ENRICHMENT_PARQUET = f"https://storage.googleapis.com/trilogy_public_models/duckdb/trees/tree_enrichment_v{DATA_VERSION}.parquet?cb={randint(0, 2**32)}"
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
#
# SPECIES_SENTINELS carries the values every ingest writes for a tree it could
# not identify (_ingest_shared: "Unknown", plus the growth-form sentinels
# "Palm" / "Shrub" / "Cactus").  They are real values in the `species` key so
# joins stay null-free, but they are not taxa and must never reach the
# enrichment LLM: asked to describe one, a model answers with a plausible,
# specific and wrong taxon.  "Unknown" was enriched once, in April 2026, and
# came back as Orania timikae — a critically endangered New Guinea palm that
# then labelled 189,139 trees across all fourteen cities, with a palm icon and
# an iNaturalist palm photo, until purge_non_taxa() removed the row.
#
# Skipping is not enough on its own: get_already_enriched() carries existing
# rows forward, so a row that got in before the exclusion existed survives
# every subsequent run.  purge_non_taxa() below is what removes it.
SKIP_SPECIES: set[str] = {
    "Scheduled Planting Site - Spring 2026",
    "Vacant Unacceptable/Retired",
    "Vacant site medium",
    *SPECIES_SENTINELS,
}

# SQL fragment for use in WHERE clauses when selecting from tree_info parquet.
# Filters out all excluded species in one place.  Built from SKIP_SPECIES so
# the SQL and the Python set cannot drift.
_EXCLUDED_SQL_LITERALS = sorted(
    {"::", "tree", "to be determine'd", ":: tree", ":: brisbane box",
     ":: to be determine"}
    | {s.lower() for s in SKIP_SPECIES}
)

SPECIES_EXCLUSION_SQL = (
    "species IS NOT NULL"
    " AND trim(species) != ''"
    " AND lower(trim(species)) NOT IN ("
    + ", ".join("'" + lit.replace("'", "''") + "'" for lit in _EXCLUDED_SQL_LITERALS)
    + ")"
)


def should_skip_species(species: str) -> bool:
    return species.strip().lower() in EXCLUDED_SPECIES


def purge_non_taxa(table):
    """Drop rows of an enrichment table whose `species` is in SKIP_SPECIES.

    Adding a value to SKIP_SPECIES only stops it being enriched *again*:
    `get_already_enriched` reads whatever the parquet holds and
    `merge_with_existing` concatenates it forward, so a row that got in before
    the exclusion existed survives every subsequent run for ever.  This is the
    step that actually removes it.  It runs on load rather than on write, so
    the purge lands in the parquet on the next enrichment whether or not any
    new species were processed.

    The row that motivated it: `species = 'Unknown'`, enriched 2026-04-04 into
    *Orania timikae*, joined to 189,139 trees across all fourteen cities.

    pyarrow is imported here rather than at module scope because
    tree_enrichment_probe.py imports this module and does not depend on it.
    """
    import pyarrow as pa
    import pyarrow.compute as pc

    if len(table) == 0:
        return table
    species = table.column("species")
    excluded = pa.array(sorted(SKIP_SPECIES), pa.string())
    # A null species is dropped too: `species` is the join key and no tree row
    # carries a null one, so such a row can only ever be dead weight.
    keep = pc.and_(
        pc.is_valid(species),
        pc.fill_null(pc.invert(pc.is_in(species, excluded)), False),
    )
    purged = table.filter(keep)
    if len(purged) != len(table):
        removed = sorted(
            set(table.column("species").to_pylist())
            - set(purged.column("species").to_pylist())
        )
        print(
            f"[purge] dropped {len(table) - len(purged)} non-taxon row(s) from "
            f"the enrichment table: {removed}",
            file=sys.stderr,
        )
    return purged
