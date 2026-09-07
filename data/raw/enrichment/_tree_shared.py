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
    SPECIES_MISSPELLINGS,
    SPECIES_SENTINELS,
    SPECIES_SYNONYMS,
    misspellings_of,
    sanitize_species,
    synonyms_of,
)
from enrichment._common_name_style import (  # noqa: E402,F401  (re-exported)
    normalize_common_name,
    normalize_common_names,
)

# What `sanitize_species` folds, this table must fold the same way: the two
# maps differ in what they claim, not in what the ingest publishes.
SPECIES_FOLDS: dict[str, str] = {**SPECIES_SYNONYMS, **SPECIES_MISSPELLINGS}

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


# Names that pair a real genus with an epithet from a different species.
#
# These are the residue of the August 2026 backlog: 795 species queued, 786
# enriched, and these nine left. Each is a chimera the source data invented --
# "Erythrina camaldulensis" is a Eucalyptus, "Pinus abies" a Picea, "Acer
# implexa" an Acacia, "Laurus lucidum" a Ligustrum, "Pinus excelsior" a
# Fraxinus, "Melaleuca azedarach" a Melia, "Cupressus plicata" a Thuja. There
# is no such tree, so there is nothing to find, and the model is right to
# return nothing: asked twice under a prompt that explicitly requests a common
# name, all nine still came back empty.
#
# `sanitize_species` cannot catch them, and should not try: both halves are
# real Latin, and the shape is indistinguishable from a correct binomial. Only
# knowing the taxonomy separates "Acer implexa" from "Acer campestre".
#
# Kept out of the *queue* but deliberately not out of SKIP_SPECIES, because
# that would also purge their rows. Whatever description the model did manage
# is better than nothing for the 30 trees involved, and the map falls back to
# the scientific name for the label -- which is the honest answer when we do
# not know what the tree is.
#
# Truncating them to the genus is wrong, not conservative: an "Acer implexa" is
# an Acacia, so calling it an Acer asserts something false.
CHIMERA_SPECIES: set[str] = {
    "Acer implexa",
    "Cupressus plicata",
    "Erythrina camaldulensis",
    "Laurus lucidum",
    "Melaleuca azedarach",
    "Pinus aberdoniae",
    "Pinus abies",
    "Pinus excelsior",
    "Tamerix angelica",
    "Ulmus paradoxa",
}


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

    CHIMERA_SPECIES is the one thing this rule cannot derive: a genus welded to
    another species' epithet is shaped exactly like a real binomial, so only
    knowing the taxonomy tells them apart.

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
    if value in CHIMERA_SPECIES:
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


def with_normalized_common_names(table):
    """Rewrite every row's `common_names` in sentence case.

    Applied on load, like `purge_non_taxa`, so the published table converges
    on one convention on the next publish rather than only for rows written
    after the rule existed: in September 2026 a fifth of the names were ALL
    CAPS and a third Title Case (`EVERGREEN PEAR`, `Evergreen Pear`,
    `evergreen pear` were all present).  Case-duplicates within a row collapse
    to one.  See `_common_name_style` for the rule and its proper-name lists.
    """
    import pyarrow as pa

    if "common_names" not in table.schema.names or len(table) == 0:
        return table
    before = table.column("common_names").to_pylist()
    after = [normalize_common_names(names) for names in before]
    changed = sum(1 for a, b in zip(before, after) if a != b)
    if not changed:
        return table
    print(
        f"[names] normalised common_names on {changed} row(s) to sentence case",
        file=sys.stderr,
    )
    idx = table.schema.get_field_index("common_names")
    return table.set_column(idx, "common_names", pa.array(after, type=table.schema.field(idx).type))


def hybrid_twins(species: str) -> list[str]:
    """The other spelling(s) of a hybrid mark, or ``[]`` for a non-hybrid.

    The ingest emits one spelling now (ASCII "x"), but a published parquet can
    still carry U+00D7 until its city is rebuilt, and `species` is the join
    key -- so a row is published under both.  Transitional: once every city
    has been refreshed past the change the U+00D7 rows are orphans.
    """
    out = []
    for a, b in ((" × ", " x "), (" x ", " × ")):
        if a in species:
            out.append(species.replace(a, b))
    return out


def alias_keys(species: str, synonyms: list[str] | None) -> list[str]:
    """Every key a row for *species* is also published under.

    Its hybrid twin, each of its synonyms, each misspelling the ingest folds
    onto it, and each of those names' hybrid twins -- all the spellings a tree
    row might still carry for this taxon.  Ordered, deduplicated, and never
    the species itself.

    The misspellings come from the code map rather than from *synonyms*, which
    is the row's own column: a typo is not a synonym and is deliberately not
    listed there, but a tree row in a city that has not rebuilt still carries
    it and still has to join.
    """
    seen: list[str] = []
    for name in [*hybrid_twins(species), *(synonyms or []), *misspellings_of(species)]:
        for key in (name, *hybrid_twins(name)):
            if key != species and key not in seen:
                seen.append(key)
    return seen


def other_names(species: str, accepted: str, synonyms: list[str]) -> list[str] | None:
    """What a row keyed *species* lists under `synonyms`: every other name of
    the taxon, hybrid-mark twins excluded (a spelling is not a synonym)."""
    names = {accepted, *synonyms} - {species}
    return sorted(names) or None


def with_species_aliases(table):
    """Fold synonyms onto their accepted row, fill `synonyms`, and publish an
    alias row under every other key a tree row might still carry.

    `SPECIES_SYNONYMS` and `SPECIES_MISSPELLINGS` (_ingest_shared) are applied
    by `sanitize_species`, so a rebuilt city publishes the accepted name only.
    Three things follow for this table, and this function does all three on
    every load:

    1. **Consolidate.**  A row keyed by a synonym or a misspelling is a
       duplicate the LLM was paid for twice.  When the accepted row exists the
       duplicate's row is dropped; when it does not, it is re-keyed to the
       accepted name so nothing is re-asked.

    2. **Fill `synonyms`.**  Each accepted row lists the *synonyms* the map
       folds into it, merged with whatever the row already carried (a reviewer
       can add one in `enrichment_admin.py` before the pair reaches the code).
       Misspellings are not listed: the column says what else the taxon is
       called, and a typo is not one of its names.

    3. **Alias.**  A copy of the row is published under every synonym key,
       every misspelling key and every hybrid-mark twin, so a tree row still
       carrying the old spelling -- a city not yet rebuilt, 158k
       `Platanus x acerifolia` rows on the day this landed and 787
       `Liquidambar stryaciflua` on the day the misspellings did -- keeps its
       common name.  The copy's own `synonyms` lists the accepted name, so a
       reader can tell which row is the taxon's.

    A parquet without a `synonyms` column gets one; a table with no such
    column at all (the older unit tests) is aliased on the hybrid mark alone.
    """
    import pyarrow as pa

    rows = table.to_pylist()
    has_synonyms = "synonyms" in table.schema.names
    index = {row["species"]: i for i, row in enumerate(rows) if row.get("species")}

    # 1. Consolidate synonym- and misspelling-keyed rows onto the accepted name.
    dropped: list[str] = []
    rekeyed: list[str] = []
    for synonym, accepted in SPECIES_FOLDS.items():
        for key in (synonym, *hybrid_twins(synonym)):
            if key not in index:
                continue
            if accepted in index or any(t in index for t in hybrid_twins(accepted)):
                rows[index[key]] = None
                dropped.append(key)
            else:
                rows[index[key]]["species"] = accepted
                index[accepted] = index[key]
                rekeyed.append(f"{key} -> {accepted}")
            del index[key]
    rows = [row for row in rows if row is not None]
    have = {row["species"] for row in rows if row.get("species")}

    # 2. Fill `synonyms` on every accepted row.
    if has_synonyms:
        for row in rows:
            species = row.get("species")
            if not species:
                continue
            merged = {*(row.get("synonyms") or []), *synonyms_of(species)} - {species}
            row["synonyms"] = sorted(merged) or None

    # 3. Alias rows.
    aliases: list[dict] = []
    for row in rows:
        species = row.get("species")
        if not species:
            continue
        synonyms = list(row.get("synonyms") or []) if has_synonyms else []
        for key in alias_keys(species, synonyms):
            if key in have:
                continue
            have.add(key)
            twin = {**row, "species": key}
            if has_synonyms:
                twin["synonyms"] = other_names(key, species, synonyms)
            aliases.append(twin)

    if dropped or rekeyed:
        print(
            f"[synonyms] folded {len(dropped) + len(rekeyed)} synonym-keyed row(s) "
            f"onto their accepted name: dropped {dropped or 'none'}; "
            f"re-keyed {rekeyed or 'none'}",
            file=sys.stderr,
        )
    if aliases:
        print(
            f"[alias] added {len(aliases)} alias row(s) so every synonym and "
            f"hybrid-mark spelling joins",
            file=sys.stderr,
        )
    if not (dropped or rekeyed or aliases or has_synonyms):
        return table
    return pa.Table.from_pylist(rows + aliases, schema=table.schema)


def published_species_keys(source: str = TREE_INFO_PARQUET) -> set[str] | None:
    """Every `species` value the published rollup currently holds, or None.

    None means "could not tell", and every caller must read it as "keep
    everything": an unreachable rollup is not evidence that a key is unused.
    """
    import duckdb

    conn = duckdb.connect()
    try:
        rows = conn.execute(
            "SELECT DISTINCT species FROM read_parquet(?) WHERE species IS NOT NULL",
            [source],
        ).fetchall()
        return {row[0] for row in rows}
    except Exception as exc:
        print(
            f"[purge] could not read published species ({exc}); keeping every row",
            file=sys.stderr,
        )
        return None
    finally:
        conn.close()


def purge_unreachable_keys(table, published: set[str] | None = None):
    """Drop rows keyed on a name no tree row can carry any more.

    `species` is the join key, and the only names that reach it are the ones
    `sanitize_species` emits.  Everything else in this table is a row nothing
    will ever join to -- 3,065 of 7,495 in the September 2026 audit, 41% of
    the table, almost all of it from before the ingest learned to truncate to
    species rank: `Abies balsamea 'nana'`, `Abies cf. sachalinensis`,
    `Abies cilicica ssp. isaurica`, `Anacardiaceae`.

    Reachable means one of two things, and the second is why this cannot be a
    one-line filter:

    * the ingest emits the name as written (`sanitize_species(s) == s`), or
    * it is an alias key of such a row -- a synonym, a misspelling or a
      hybrid-mark twin, which `with_species_aliases` has just published on
      purpose so a city that has not rebuilt still joins.

    So this runs **after** the alias step, never before.  Run before it, the
    re-key branch would never fire: a row keyed on a synonym whose accepted
    row does not exist yet is not junk, it is the enrichment for that taxon
    under an old name, and dropping it would pay the LLM again to learn what
    the table already knew.

    A row `sanitize_species` maps to None is kept, which is deliberately
    conservative.  Those are the sentinels and the nothogenus names -- and
    `Unknown` alone is the join key for 1.4 million trees, so the cost of
    being wrong in that direction is not symmetric.  `purge_non_taxa` already
    removes the ones that are genuinely junk, by name.

    So is *published*, and for a sharper reason: "the ingest would rewrite it"
    is a statement about the next rebuild, not about what is on GCS now.  Every
    tightening of `sanitize_species` orphans a batch of keys that cities go on
    publishing until each one rebuilds -- adding `genus` to the placeholder
    epithets orphaned 17 of them, still carrying 1,162 trees between them --
    and dropping those rows would blank a label that is currently rendering.
    Pass the published keys and they are kept until the rebuild catches up;
    pass None (a test, or an unreachable rollup) and nothing is protected,
    which is why the caller reads it rather than this deciding for itself.
    """
    import pyarrow as pa

    if len(table) == 0:
        return table
    published = published or set()
    rows = table.to_pylist()
    emitted = {
        row["species"]
        for row in rows
        if row.get("species") and sanitize_species(row["species"]) == row["species"]
    }
    reachable = set(emitted)
    for row in rows:
        species = row.get("species")
        if species in emitted:
            reachable.update(alias_keys(species, list(row.get("synonyms") or [])))

    kept = [
        row
        for row in rows
        if not row.get("species")
        or row["species"] in reachable
        or row["species"] in published
        or sanitize_species(row["species"]) is None
    ]
    if len(kept) != len(rows):
        dropped = sorted(
            {r["species"] for r in rows if r.get("species")}
            - {r["species"] for r in kept if r.get("species")}
        )
        print(
            f"[purge] dropped {len(rows) - len(kept)} row(s) keyed on a name the "
            f"ingest can no longer emit, e.g. {dropped[:5]}",
            file=sys.stderr,
        )
        return pa.Table.from_pylist(kept, schema=table.schema)
    return table


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
