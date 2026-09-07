"""The enrichment table must never hold a row for a non-taxon.

`species` is the join key from every tree row into the enrichment table, so a
junk row there is not inert: it labels every tree carrying that value.  The
sentinel `Unknown` was enriched once, in April 2026, and came back as *Orania
timikae* — a critically endangered New Guinea palm.  It sat in the table for
four months and, once UNKNOWN_SPECIES adopted the same string, labelled 189,139
trees across all fourteen cities with a palm icon, a palm photo and that
description.

Excluding a value from enrichment is only half the fix; the other half is
removing the row that is already there, because the existing table is carried
forward on every run.
"""

from __future__ import annotations

import re
import sys
from datetime import timedelta
from pathlib import Path

import pyarrow as pa
import pytest

RAW_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAW_DIR))

from _ingest_shared import SENTINEL_ENRICHMENT, SPECIES_SENTINELS  # noqa: E402
from enrichment._tree_shared import (  # noqa: E402
    CHIMERA_SPECIES,
    ENRICHMENT_COMPLETE_SQL,
    with_species_aliases,
    REENRICH_INCOMPLETE_BEFORE,
    SKIP_SPECIES,
    SPECIES_EXCLUSION_SQL,
    is_enrichable_species,
    purge_non_taxa,
    published_species_keys,
    purge_unreachable_keys,
    sentinel_enrichment_rows,
    with_sentinel_rows,
)

SPECIES_TS = RAW_DIR.parents[1] / "src" / "src" / "data" / "species.ts"


def _table(species: list[str | None]) -> pa.Table:
    return pa.table({"species": pa.array(species, type=pa.string())})


def test_every_ingest_sentinel_is_excluded():
    """A sentinel added in _ingest_shared must not need a second edit here."""
    assert SPECIES_SENTINELS <= SKIP_SPECIES


@pytest.mark.parametrize("value", sorted(SPECIES_SENTINELS))
def test_exclusion_sql_covers_each_sentinel(value: str):
    assert f"'{value.lower()}'" in SPECIES_EXCLUSION_SQL


def test_purge_removes_skipped_species():
    out = purge_non_taxa(_table(["Acer rubrum", "Unknown", "Palm", "Quercus robur"]))
    assert out.column("species").to_pylist() == ["Acer rubrum", "Quercus robur"]


def test_purge_removes_null_species():
    out = purge_non_taxa(_table(["Acer rubrum", None]))
    assert out.column("species").to_pylist() == ["Acer rubrum"]


def test_purge_keeps_a_clean_table_unchanged():
    table = _table(["Acer rubrum", "Quercus robur"])
    assert purge_non_taxa(table).column("species").to_pylist() == [
        "Acer rubrum",
        "Quercus robur",
    ]


def test_purge_handles_an_empty_table():
    assert len(purge_non_taxa(_table([]))) == 0


def test_purge_reports_what_it_dropped(capsys):
    purge_non_taxa(_table(["Acer rubrum", "Unknown"]))
    assert "Unknown" in capsys.readouterr().err


# ── Authored sentinel rows ─────────────────────────────────────────────────────
#
# A sentinel row is the opposite of the one that caused the purge: purge_non_taxa
# removes whatever the parquet holds for a sentinel, and these put back a row
# nobody guessed.  The tests below pin both halves — the values, and the fact
# that the row says nothing it has no business saying.


def _sentinel_table(species: list[str | None]) -> pa.Table:
    """A table shaped like the enrichment parquet's relevant columns."""
    return pa.table(
        {
            "species": pa.array(species, type=pa.string()),
            "common_names": pa.array([None] * len(species), type=pa.list_(pa.string())),
            "description": pa.array([None] * len(species), type=pa.string()),
            "tree_form": pa.array([None] * len(species), type=pa.string()),
            "is_complete": pa.array([None] * len(species), type=pa.bool_()),
            "enriched_at": pa.array(
                [None] * len(species), type=pa.timestamp("us", tz="UTC")
            ),
        }
    )


def test_every_sentinel_has_an_authored_row():
    assert set(SENTINEL_ENRICHMENT) == set(SPECIES_SENTINELS)
    assert {row["species"] for row in sentinel_enrichment_rows()} == set(SPECIES_SENTINELS)


@pytest.mark.parametrize("row", sentinel_enrichment_rows(), ids=lambda r: r["species"])
def test_sentinel_row_carries_a_label_and_a_form(row: dict):
    assert row["common_names"] and row["common_names"][0].strip()
    assert row["tree_form"].strip()
    assert row["description"].strip()


@pytest.mark.parametrize("row", sentinel_enrichment_rows(), ids=lambda r: r["species"])
def test_sentinel_row_claims_nothing_it_cannot_know(row: dict):
    """The Orania timikae row had a genus, a photo and a description of a real
    palm. A sentinel is not a taxon: it may name itself and the growth form its
    source recorded, and nothing else."""
    for field in (
        "genus",
        "species_epithet",
        "family",
        "photo_url",
        "photo_attribution",
        "native_ecoregions",
        "usda_zone_min",
        "usda_zone_max",
    ):
        assert row.get(field) is None, f"{row['species']} claims {field}"
    assert row["is_complete"] is False


def test_purge_then_append_replaces_a_drifted_row():
    """The self-healing property: whatever the parquet holds for a sentinel is
    dropped and the authored row put back, so one bad row cannot persist."""
    drifted = _sentinel_table(["Acer rubrum", "Unknown"])
    drifted = drifted.set_column(
        drifted.schema.get_field_index("tree_form"),
        "tree_form",
        pa.array([None, "palm"], type=pa.string()),
    )

    out = with_sentinel_rows(purge_non_taxa(drifted))
    by_species = {
        row["species"]: row for row in out.to_pylist()
    }

    assert by_species["Acer rubrum"]["tree_form"] is None
    assert by_species["Unknown"]["tree_form"] == SENTINEL_ENRICHMENT["Unknown"]["tree_form"]
    assert len(out) == 1 + len(SPECIES_SENTINELS)
    assert out.column("species").to_pylist().count("Unknown") == 1


def test_sentinel_rows_match_the_frontend():
    """src/src/data/species.ts hardcodes the same labels for the map. The two
    are edited by hand in different languages; this is what catches the drift."""
    source = SPECIES_TS.read_text(encoding="utf-8")
    entries = re.findall(
        r"species:\s*(\w+),\s*label:\s*'([^']*)',\s*treeForm:\s*'([^']*)',\s*note:\s*'([^']*)'",
        source,
    )
    constants = dict(re.findall(r"export const (\w+_SPECIES) = '([^']*)'", source))
    assert entries, f"could not parse sentinels out of {SPECIES_TS}"

    frontend = {
        constants[name]: {"label": label, "tree_form": form, "note": note}
        for name, label, form, note in entries
    }
    assert set(frontend) == set(SENTINEL_ENRICHMENT)
    for species, values in frontend.items():
        authored = SENTINEL_ENRICHMENT[species]
        assert authored["common_names"][0] == values["label"], species
        assert authored["tree_form"] == values["tree_form"], species
        assert authored["description"] == values["note"], species


# ---------------------------------------------------------------------------
# is_enrichable_species
# ---------------------------------------------------------------------------
#
# The lists above name specific values.  This is the general rule, and it is
# the one that keeps the queue honest as new cities land: a species is
# enrichable when the ingest would keep it exactly as written.  It is the same
# question `sanitize_species` answers, asked from the other side.


@pytest.mark.parametrize(
    "value",
    [
        "Acer rubrum",
        "Platanus x hispanica",
        "Citrus x limon",
        "Quercus",
        # Badly typed is not the same as not a taxon -- the LLM resolves these,
        # and skipping them would lose a tree we can identify.
        "Crateagus monogyna",
        "Sequioa sempervirens",
    ],
)
def test_a_taxon_is_enrichable(value: str):
    assert is_enrichable_species(value) is True


@pytest.mark.parametrize("value", sorted(SPECIES_SENTINELS))
def test_a_sentinel_is_never_enrichable(value: str):
    assert is_enrichable_species(value) is False


@pytest.mark.parametrize(
    "value",
    [
        # Not a taxon at all: the ingest drops these to a sentinel.
        "Oak", "Japonica", "Kastanie", "X ambigua", "Platanaceae",
        "Mixed species", "Tai haku",
        # A value the ingest *rewrites* is skipped too, rather than enriched
        # under its raw spelling: the next refresh publishes these trees as
        # "Acer" and "Prunus", so a row keyed on the raw string is dead on
        # arrival -- a duplicate of an entry that already exists, paid for.
        "Acer unidentified", "Prunus tai", "Parkinsonia x",
        # Same reason: the ingest now emits one hybrid spelling, so a U+00D7
        # value is one it rewrites.  with_hybrid_aliases keeps the join working
        # in the meantime -- see test_hybrid_alias_bridges_both_spellings.
        "Citrus × limon",
        None, "", "   ",
    ],
)
def test_a_non_taxon_is_not_enrichable(value):
    assert is_enrichable_species(value) is False


def test_the_queue_rule_and_the_ingest_cannot_drift():
    """Not a restatement of the cases above: this is the property they sample.

    Tying the queue to `sanitize_species` is what makes an improvement to the
    ingest's idea of "is this a taxon" shrink the enrichment backlog in the
    same edit, with no second list to keep in step.
    """
    from _ingest_shared import sanitize_species

    for value in ("Acer rubrum", "Oak", "Acer unidentified", "Crateagus monogyna"):
        assert is_enrichable_species(value) is (sanitize_species(value) == value)


# ---------------------------------------------------------------------------
# ENRICHMENT_COMPLETE_SQL / REENRICH_INCOMPLETE_BEFORE
# ---------------------------------------------------------------------------
#
# 226 species carried a row with a null `common_names`.  `get_already_enriched`
# read that as "done" and never revisited them; the freshness probe wanted a
# common name and reported them missing on every run.  Neither side was wrong
# on its own -- they were answering different questions.  These pin the shared
# answer, and the bound that stops the retry looping.


def _rows(conn, table_sql: str, where: str, *params):
    conn.execute(f"CREATE OR REPLACE TABLE t AS {table_sql}")
    rows = conn.execute(f"SELECT species FROM t WHERE {where}", list(params)).fetchall()
    return [r[0] for r in rows]


@pytest.fixture()
def conn():
    duckdb = pytest.importorskip("duckdb")
    c = duckdb.connect()
    yield c
    c.close()


ROWS_SQL = """
SELECT * FROM (VALUES
    ('Acer rubrum',   ['red maple'], 'broadleaf'),
    ('Hovenia tomentella', NULL,     'broadleaf'),
    ('Enkianthus deflexus', [],      'broadleaf'),
    ('Corylopsis glandulifera', ['winterhazel'], NULL)
) AS v(species, common_names, tree_form)
"""


def test_complete_means_a_common_name_and_a_form(conn):
    """The bar is what the map renders: the worker takes `common_name` from the
    first entry of `common_names` and the icon and colour from `tree_form`.
    Everything else is detail a page shows if it has it -- a stricter bar would
    be permanently unmet, because plenty of real taxa have no published canopy
    spread and asking again does not conjure one."""
    assert _rows(conn, ROWS_SQL, ENRICHMENT_COMPLETE_SQL) == ["Acer rubrum"]


def test_an_empty_common_names_list_is_not_a_common_name(conn):
    """A null and an empty list mean the same thing and must classify alike."""
    incomplete = _rows(conn, ROWS_SQL, f"NOT ({ENRICHMENT_COMPLETE_SQL})")
    assert "Enkianthus deflexus" in incomplete
    assert "Hovenia tomentella" in incomplete


def test_the_retry_window_closes_behind_itself(conn):
    """Why a date and not a retry counter: it converges with no schema change.

    A row rewritten by this run carries today's `enriched_at`, so it falls out
    of scope on the next one.  A species the model still cannot name is retried
    exactly once -- without the bound it would be re-enriched on every refresh
    tick, for ever.
    """
    # Derived from the constant rather than hardcoded: the date moves whenever
    # someone deliberately asks for another attempt, and this property holds
    # across every such move.
    after = (REENRICH_INCOMPLETE_BEFORE + timedelta(hours=1)).isoformat()
    before = (REENRICH_INCOMPLETE_BEFORE - timedelta(days=1)).isoformat()
    stamped = f"""
    SELECT * FROM (VALUES
        ('Retried today',   TIMESTAMPTZ '{after}'),
        ('Never revisited', TIMESTAMPTZ '{before}')
    ) AS v(species, enriched_at)
    """
    eligible = _rows(
        conn, stamped, "enriched_at IS NULL OR enriched_at < ?", REENRICH_INCOMPLETE_BEFORE
    )
    assert eligible == ["Never revisited"]


def test_the_probe_reads_the_shared_definition():
    """The run and the probe must not disagree about what the table is owed."""
    probe = (RAW_DIR / "tree_enrichment_probe.py").read_text(encoding="utf-8")
    assert "ENRICHMENT_COMPLETE_SQL" in probe


# ---------------------------------------------------------------------------
# with_species_aliases: the hybrid-mark twin
# ---------------------------------------------------------------------------


def _hybrid_table(species: list[str]) -> pa.Table:
    return pa.table({
        "species": pa.array(species, type=pa.string()),
        "common_names": pa.array([["london plane"]] * len(species), type=pa.list_(pa.string())),
    })


def test_hybrid_alias_bridges_both_spellings():
    """The ingest emits one spelling; the published data still carries the other
    until a refresh. `species` is the join key, so without the alias the most
    common tree in the dataset -- Paris publishes 38,845 rows of
    "Platanus × hispanica" -- loses its common name the moment that city
    rebuilds."""
    out = with_species_aliases(_hybrid_table(["Platanus × hispanica"]))
    assert sorted(out.column("species").to_pylist()) == [
        "Platanus x hispanica",
        "Platanus × hispanica",
    ]
    assert out.column("common_names").to_pylist() == [["london plane"]] * 2


def test_hybrid_alias_works_in_both_directions():
    """Cities are refreshed one at a time and in no particular order, so the
    row that exists first may be either spelling."""
    out = with_species_aliases(_hybrid_table(["Platanus x hispanica"]))
    assert "Platanus × hispanica" in out.column("species").to_pylist()


def test_hybrid_alias_does_not_duplicate_an_existing_twin():
    both = ["Alnus x spaethii", "Alnus × spaethii"]
    out = with_species_aliases(_hybrid_table(both))
    assert sorted(out.column("species").to_pylist()) == sorted(both)


def test_hybrid_alias_leaves_non_hybrids_alone():
    out = with_species_aliases(_hybrid_table(["Acer rubrum"]))
    assert out.column("species").to_pylist() == ["Acer rubrum"]


# ---------------------------------------------------------------------------
# CHIMERA_SPECIES
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", sorted(CHIMERA_SPECIES))
def test_a_chimera_is_never_queued(value: str):
    """Asked twice under a prompt that explicitly requests a common name, all
    of these came back empty -- because there is no such tree. "Acer implexa"
    is an Acacia, "Erythrina camaldulensis" a Eucalyptus, "Pinus excelsior" a
    Fraxinus."""
    assert is_enrichable_species(value) is False


def test_a_chimera_keeps_its_row():
    """Excluded from the queue but deliberately not from SKIP_SPECIES, which
    would purge the row. Whatever the model did manage beats nothing, and the
    map falls back to the scientific name for the label."""
    assert not (CHIMERA_SPECIES & SKIP_SPECIES)
    kept = purge_non_taxa(_table(sorted(CHIMERA_SPECIES)))
    assert len(kept) == len(CHIMERA_SPECIES)


def test_a_chimera_is_shaped_like_a_real_binomial():
    """Why this has to be a list and not a rule: sanitize_species sees a real
    genus and a real Latin epithet, exactly like a correct name. Only knowing
    the taxonomy separates "Acer implexa" from "Acer campestre"."""
    from _ingest_shared import sanitize_species

    for value in CHIMERA_SPECIES:
        assert sanitize_species(value) == value, value


# ---------------------------------------------------------------------------
# with_species_aliases: synonyms
# ---------------------------------------------------------------------------


def _synonym_table(rows: dict[str, list[str] | None]) -> pa.Table:
    """species -> synonyms, with a distinct common name per row so a test
    can tell which row's values survived."""
    return pa.table({
        "species": pa.array(list(rows), type=pa.string()),
        "common_names": pa.array([[f"name of {s}"] for s in rows], type=pa.list_(pa.string())),
        "synonyms": pa.array(list(rows.values()), type=pa.list_(pa.string())),
    })


def _by_species(table: pa.Table) -> dict[str, dict]:
    return {r["species"]: r for r in table.to_pylist()}


def test_a_synonym_row_is_dropped_when_the_accepted_row_exists():
    """Two rows for one taxon: the LLM was paid twice, and every species
    rollup counted the London plane as two species."""
    out = _by_species(with_species_aliases(_synonym_table({
        "Platanus x acerifolia": None,
        "Platanus x hispanica": None,
    })))
    assert out["Platanus x hispanica"]["common_names"] == ["name of Platanus x hispanica"]
    # The synonym key is still published -- as an alias of the accepted row.
    assert out["Platanus x acerifolia"]["common_names"] == ["name of Platanus x hispanica"]


def test_a_synonym_row_is_rekeyed_when_the_accepted_row_is_missing():
    """Nothing to re-ask: the row exists, only its key is out of date."""
    out = _by_species(with_species_aliases(_synonym_table({"Sophora japonica": None})))
    assert out["Styphnolobium japonicum"]["common_names"] == ["name of Sophora japonica"]
    assert out["Sophora japonica"]["common_names"] == ["name of Sophora japonica"]


def test_the_accepted_row_lists_its_synonyms_and_the_alias_points_back():
    out = _by_species(with_species_aliases(_synonym_table({"Platanus x hispanica": None})))
    assert out["Platanus x hispanica"]["synonyms"] == ["Platanus acerifolia", "Platanus hispanica", "Platanus x acerifolia"]
    assert out["Platanus x acerifolia"]["synonyms"] == ["Platanus acerifolia", "Platanus hispanica", "Platanus x hispanica"]


def test_every_synonym_key_gets_an_alias_row():
    """A tree row still carrying the old name -- a city not yet rebuilt --
    keeps its common name only if the old key still joins."""
    out = _by_species(with_species_aliases(_synonym_table({"Cupressus x leylandii": None})))
    for key in ("Cuprocyparis leylandii", "X cupressocyparis leylandii", "Cupressocyparis leylandii",
                "X cuprocyparis leylandii", "Cupressus \u00d7 leylandii"):
        assert out[key]["common_names"] == ["name of Cupressus x leylandii"], key


def test_a_hand_added_synonym_is_kept_and_aliased():
    """The admin form can add a synonym before the pair reaches the code."""
    out = _by_species(with_species_aliases(_synonym_table({"Acer rubrum": ["Acer rubrum-flavum"]})))
    assert out["Acer rubrum"]["synonyms"] == ["Acer rubrum-flavum"]
    assert out["Acer rubrum-flavum"]["common_names"] == ["name of Acer rubrum"]
    assert out["Acer rubrum-flavum"]["synonyms"] == ["Acer rubrum"]


def test_hand_added_and_code_synonyms_merge():
    out = _by_species(with_species_aliases(_synonym_table({"Platanus x hispanica": ["Platanus orientalis-hybrida"]})))
    assert out["Platanus x hispanica"]["synonyms"] == ["Platanus acerifolia", "Platanus hispanica", "Platanus orientalis-hybrida", "Platanus x acerifolia"]


def test_aliasing_is_idempotent():
    once = with_species_aliases(_synonym_table({"Platanus x hispanica": None, "Acer rubrum": None}))
    twice = with_species_aliases(once)
    assert sorted(once.column("species").to_pylist()) == sorted(twice.column("species").to_pylist())
    assert _by_species(once) == _by_species(twice)


def test_a_synonym_is_never_queued_for_enrichment():
    """`sanitize_species` rewrites it, and the queue rule defers to the ingest."""
    assert not is_enrichable_species("Platanus x acerifolia")
    assert not is_enrichable_species("Sophora japonica")
    assert is_enrichable_species("Platanus x hispanica")


# ---------------------------------------------------------------------------
# with_species_aliases: misspellings
# ---------------------------------------------------------------------------


def test_a_misspelled_row_is_dropped_when_the_correct_row_exists():
    """The same duplication a synonym causes, from a typo instead: two rows,
    two LLM calls and two entries in every rollup for one taxon."""
    out = _by_species(with_species_aliases(_synonym_table({
        "Acer platenoides": None,
        "Acer platanoides": None,
    })))
    assert out["Acer platanoides"]["common_names"] == ["name of Acer platanoides"]
    assert out["Acer platenoides"]["common_names"] == ["name of Acer platanoides"]


def test_a_misspelled_row_is_rekeyed_when_the_correct_row_is_missing():
    out = _by_species(with_species_aliases(_synonym_table({"Acer platenoides": None})))
    assert out["Acer platanoides"]["common_names"] == ["name of Acer platenoides"]


def test_every_misspelling_key_gets_an_alias_row():
    """This is what keeps the trees labelled between the merge and the city's
    next rebuild -- 787 `Liquidambar stryaciflua` on the day this landed."""
    out = _by_species(with_species_aliases(_synonym_table({"Liquidambar styraciflua": None})))
    assert out["Liquidambar stryaciflua"]["common_names"] == ["name of Liquidambar styraciflua"]


def test_a_misspelling_is_not_listed_as_a_synonym_of_the_taxon():
    """The column says what else the taxon is called.  A typo is not one of
    its names, so it is aliased without being claimed."""
    out = _by_species(with_species_aliases(_synonym_table({"Acer platanoides": None})))
    assert out["Acer platanoides"]["synonyms"] is None
    # ...while the alias still points home, so a reader can navigate.
    assert out["Acer platenoides"]["synonyms"] == ["Acer platanoides"]


def test_a_misspelling_is_never_queued_for_enrichment():
    assert not is_enrichable_species("Acer platenoides")
    assert not is_enrichable_species("Liquidambar stryaciflua")
    assert is_enrichable_species("Acer platanoides")


# ---------------------------------------------------------------------------
# purge_unreachable_keys
# ---------------------------------------------------------------------------


def test_a_key_the_ingest_can_no_longer_emit_is_dropped():
    """41% of the table in the September 2026 audit: rows written before the
    ingest truncated to species rank, which no tree row can join to."""
    out = _by_species(purge_unreachable_keys(_synonym_table({
        "Abies balsamea": None,
        "Abies balsamea 'nana'": None,
        "Abies cilicica ssp. isaurica": None,
    })))
    assert set(out) == {"Abies balsamea"}


def test_an_alias_row_is_not_unreachable():
    """The alias step publishes these on purpose, for a city that has not
    rebuilt -- purging them would undo it in the same pass."""
    table = with_species_aliases(_synonym_table({"Platanus x hispanica": None}))
    out = _by_species(purge_unreachable_keys(table))
    for key in ("Platanus x acerifolia", "Platanus acerifolia", "Platanus hispanica"):
        assert key in out, key


def test_a_misspelling_alias_survives_the_purge():
    table = with_species_aliases(_synonym_table({"Liquidambar styraciflua": None}))
    out = _by_species(purge_unreachable_keys(table))
    assert "Liquidambar stryaciflua" in out


def test_a_hand_added_synonym_survives_the_purge():
    """It is not in the code map, so reachability has to read the row's own
    `synonyms` column rather than only the maps."""
    table = with_species_aliases(_synonym_table({"Acer rubrum": ["Acer rubrum-flavum"]}))
    out = _by_species(purge_unreachable_keys(table))
    assert "Acer rubrum-flavum" in out


def test_a_sentinel_is_never_purged_as_unreachable():
    """`sanitize_species` returns None for these, and `Unknown` alone is the
    join key for 1.4 million trees."""
    out = _by_species(purge_unreachable_keys(_synonym_table(
        {name: None for name in sorted(SPECIES_SENTINELS)}
    )))
    assert set(out) == set(SPECIES_SENTINELS)


def test_the_purge_is_idempotent():
    table = with_species_aliases(_synonym_table({
        "Platanus x hispanica": None,
        "Abies balsamea 'nana'": None,
    }))
    once = purge_unreachable_keys(table)
    assert _by_species(once) == _by_species(purge_unreachable_keys(once))


def test_a_key_trees_still_carry_is_kept_even_when_unreachable():
    """Every tightening of `sanitize_species` orphans keys that cities go on
    publishing until each rebuilds.  Adding `genus` to the placeholder epithets
    orphaned 17 of them, carrying 1,162 trees; dropping those rows would blank
    a label that is currently rendering."""
    table = _synonym_table({"Malus": None, "Malus genus": None})
    assert "Malus genus" not in _by_species(purge_unreachable_keys(table))
    kept = _by_species(purge_unreachable_keys(table, published={"Malus genus"}))
    assert "Malus genus" in kept


def test_an_unreadable_rollup_protects_nothing_but_is_reported():
    """`published_species_keys` returns None when it cannot read the rollup,
    and the caller must not treat that as 'no key is in use'."""
    assert published_species_keys("https://example.invalid/nope.parquet") is None
