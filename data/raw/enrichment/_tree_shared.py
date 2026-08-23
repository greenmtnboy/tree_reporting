# Shared constants for tree_enrichment.py and tree_enrichment_probe.py.
# Both scripts import from here so exclusion logic stays in one place.

import sys
from datetime import datetime, timezone

from trilogy import Environment
from pathlib import Path
from random import randint

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _ingest_shared import (  # noqa: E402
    SENTINEL_ENRICHMENT,
    SPECIES_SENTINELS,
    sanitize_species,
)

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


# What "enriched" means, as one SQL fragment both scripts read.
#
# The bar is deliberately low: a common name and a growth form.  Those two are
# what the map renders -- the worker derives `common_name` from
# `split_part(common_names, ',', 1)` and picks the icon and colour from
# `tree_form` -- and everything else on the row is detail a page shows if it
# has it.  A stricter bar would be permanently unmet: plenty of real taxa have
# no published canopy spread, and asking again does not conjure one.
ENRICHMENT_COMPLETE_SQL = (
    "common_names IS NOT NULL"
    " AND array_length(common_names) > 0"
    " AND tree_form IS NOT NULL"
)

# Incomplete rows enriched before this are tried once more.
#
# 226 species carried a row with a null `common_names`, written between March
# and August 2026.  `get_already_enriched` treated "a row exists" as "done", so
# nothing ever revisited them -- and because the freshness probe wants a common
# name, it could never report `true` no matter how many runs completed.  The
# rows were not unfixable: asked directly, a model names *Hovenia tomentella*
# the downy Japanese raisin tree and *Corylopsis glandulifera* the Chinese
# fragrant winterhazel.
#
# A fixed date rather than a retry counter, because it converges without a
# schema change: a row rewritten today carries today's `enriched_at` and falls
# out of scope, so a species the model still cannot name is retried exactly
# once and then left alone.  Without that, an unnameable species would be
# re-enriched on every refresh tick, for ever.  Moving this date is how you ask
# for another attempt -- a deliberate, greppable edit, like SENTINEL_ENRICHED_AT.
#
# Moved once, on 2026-08-23, after the prompt was fixed to ask for
# `common_names` and `tree_form` at all.  The 189 rows still short at that point
# were written by the older prompt -- 176 of them carrying tree_form="default",
# the signature of an answer produced without being asked -- so they deserved
# one attempt under the new one.  The value must sit *after* the rows being
# re-queued and *before* the run that rewrites them, or the retry stops
# converging: pick a timestamp, not a date, when moving it the same day.
REENRICH_INCOMPLETE_BEFORE = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)


def should_skip_species(species: str) -> bool:
    return species.strip().lower() in EXCLUDED_SPECIES


def is_enrichable_species(value: str | None) -> bool:
    """Is *value* a taxon worth asking the LLM about?

    The lists above name specific values.  This is the general rule, and it
    defers to the ingest: a species is enrichable when `sanitize_species`
    would keep it exactly as written.  Tying the two together means an
    improvement to the ingest's idea of "is this a taxon" shrinks the
    enrichment queue in the same edit, with no second list to keep in step --
    and 141 of the 795 species queued in August 2026 turned out to be things
    like "Oak", "Japonica", "Kastanie" and "X ambigua".

    A value `sanitize_species` *rewrites* is skipped too, not enriched under
    its raw spelling.  "Acer unidentified" is a row the next refresh will
    publish as "Acer", so a row keyed on the raw string is dead on arrival --
    it buys a duplicate of an entry that already exists, and pays the LLM for
    it.  Both cases leave those trees unenriched until the refresh rewrites
    their species, which is where they already were.
    """
    if not value:
        return False
    if value in SKIP_SPECIES or should_skip_species(value):
        return False
    return sanitize_species(value) == value


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


# Authored sentinel rows carry a fixed timestamp rather than "now": they are not
# the product of an enrichment run, and a moving value would rewrite the parquet
# on every run for no reason.
SENTINEL_ENRICHED_AT = datetime(2026, 8, 21, tzinfo=timezone.utc)


def sentinel_enrichment_rows() -> list[dict]:
    """The canonical enrichment row for each species sentinel.

    A sentinel is a real value in the `species` key, and `species` is the join
    key into enrichment — so with no row here, every enrichment column comes
    back NULL for the ~190k trees whose source did not identify them, including
    `species` itself once a query reads any enrichment field.  These rows give
    the join something to land on.

    They are deliberately thin: the label, the growth form the source actually
    recorded, and a description saying so.  No taxonomy, no photo, no ecological
    claims — a sentinel is not a taxon, and the values here are authored to
    match src/src/data/species.ts rather than generated.
    """
    return [
        {
            "species": species,
            **values,
            "is_complete": False,
            "enriched_at": SENTINEL_ENRICHED_AT,
        }
        for species, values in sorted(SENTINEL_ENRICHMENT.items())
    ]


def with_hybrid_aliases(table):
    """Give every U+00D7-keyed row an ASCII twin, and vice versa.

    The ingest emits one hybrid spelling now (ASCII "x"), but that only reaches
    the data on the next full refresh -- until then the published parquets still
    carry "Platanus × hispanica" while a freshly enriched row would be keyed
    "Platanus x hispanica".  `species` is the join key, so during that window one
    of the two finds nothing: Paris alone has 38,845 rows under the U+00D7
    spelling, and the most common tree in the dataset would silently lose its
    common name the moment that city rebuilt.

    Copying the row under both keys costs 68 rows and no LLM call, and works in
    either direction, so the order in which cities get refreshed does not matter.

    **This is transitional.**  Once every city has been refreshed past the
    change, the U+00D7 rows are orphans and this should become a purge instead.
    """
    import pyarrow as pa

    rows = table.to_pylist()
    have = {row["species"] for row in rows}
    aliases = []
    for row in rows:
        species = row["species"] or ""
        for a, b in ((" × ", " x "), (" x ", " × ")):
            if a in species:
                twin = species.replace(a, b)
                if twin not in have:
                    have.add(twin)
                    aliases.append({**row, "species": twin})
    if not aliases:
        return table
    print(
        f"[hybrid] added {len(aliases)} alias row(s) so both mark spellings join",
        file=sys.stderr,
    )
    return pa.concat_tables([table, pa.Table.from_pylist(aliases, schema=table.schema)])


def with_sentinel_rows(table):
    """Append the canonical sentinel rows to *table*.

    Call it right after purge_non_taxa, which has just removed every sentinel
    row the parquet held: together they replace whatever was there with the
    authored values, so a drifted row — or one the LLM wrote before the
    exclusion existed — cannot survive a run.

    The rows are built against *table*'s own schema rather than a copy of it,
    so a schema change cannot leave these behind.
    """
    import pyarrow as pa

    rows = pa.Table.from_pylist(sentinel_enrichment_rows(), schema=table.schema)
    return pa.concat_tables([table, rows])
