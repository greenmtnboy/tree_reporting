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

import sys
from pathlib import Path

import pyarrow as pa
import pytest

RAW_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAW_DIR))

from _ingest_shared import SPECIES_SENTINELS  # noqa: E402
from enrichment._tree_shared import (  # noqa: E402
    SKIP_SPECIES,
    SPECIES_EXCLUSION_SQL,
    purge_non_taxa,
)


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
