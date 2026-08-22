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
from pathlib import Path

import pyarrow as pa
import pytest

RAW_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAW_DIR))

from _ingest_shared import SENTINEL_ENRICHMENT, SPECIES_SENTINELS  # noqa: E402
from enrichment._tree_shared import (  # noqa: E402
    ENRICHMENT_COMPLETE_SQL,
    REENRICH_INCOMPLETE_BEFORE,
    SKIP_SPECIES,
    SPECIES_EXCLUSION_SQL,
    is_enrichable_species,
    purge_non_taxa,
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
        "Citrus × limon",
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
    stamped = """
    SELECT * FROM (VALUES
        ('Retried today',  TIMESTAMPTZ '2026-08-22 12:00:00+00'),
        ('Never revisited', TIMESTAMPTZ '2026-03-29 14:25:12+00')
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
