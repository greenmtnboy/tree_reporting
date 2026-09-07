"""`_common_name_species`, the common-name -> binomial table.

The table is curated by hand, so what a test can add is the mechanical half:
that every value is a name the ingest would keep as written, that every key is
in normalised form (a key that is not can never be looked up), and that the
normaliser does the four inversions the wired portals actually need.

The taxonomy itself is not testable here and is not meant to be -- it is
reviewed by reading the file, the same way `SPECIES_SYNONYMS` and
`_NON_TAXON_REWRITES` are.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

RAW_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAW_DIR))

from _common_name_species import (  # noqa: E402
    COMMON_NAME_SPECIES,
    common_name_key,
    species_from_common_name,
)
from _ingest_shared import (  # noqa: E402
    SPECIES_SENTINELS,
    sanitize_species,
)


@pytest.mark.parametrize("key,value", sorted(COMMON_NAME_SPECIES.items()))
def test_every_value_is_a_name_the_ingest_keeps(key: str, value: str):
    """`sanitize_species` must return the value unchanged.

    `species` is the join key into the enrichment table, and
    `enforce_tree_schema` puts every value through `sanitize_species` before
    publishing it.  A value that gets rewritten there (a cultivar left in, a
    U+00D7 hybrid mark, a lowercase genus) would publish as something other
    than what this table says, and a value that gets *rejected* would publish
    as `Unknown` -- so the table would look right and do nothing.
    """
    assert sanitize_species(value) == value, (
        f"{key!r} -> {value!r} is not a name sanitize_species keeps as written; "
        f"it emits {sanitize_species(value)!r}"
    )


@pytest.mark.parametrize("key", sorted(COMMON_NAME_SPECIES))
def test_every_key_is_in_normalised_form(key: str):
    """A key not in `common_name_key` form is dead: nothing can ever match it."""
    assert common_name_key(key) == key


def test_no_value_is_also_a_key():
    """One lookup is enough -- the table is not chained.

    The same rule `SPECIES_SYNONYMS` follows.  A scientific name appearing as
    a key would mean a caller's result depended on how many times it looked up.
    """
    overlap = set(COMMON_NAME_SPECIES) & set(COMMON_NAME_SPECIES.values())
    assert not overlap


def test_no_value_is_a_sentinel():
    """A sentinel is not a taxon and must not be reachable from this table.

    `species_from_common_name` returns a sentinel by asking
    `form_sentinel_for`, which is the one place that decision lives.  A
    sentinel hardcoded here would be a second, silent copy of it.
    """
    assert not set(COMMON_NAME_SPECIES.values()) & SPECIES_SENTINELS


@pytest.mark.parametrize(
    "raw,expected",
    [
        # Burlington inverts on a spaced hyphen.
        ("MAPLE - NORWAY", "norway maple"),
        ("BUCKEYE- OHIO", "ohio buckeye"),
        ("SWEETGUM -ROTUNDILOBA", "rotundiloba sweetgum"),
        # ... and a bare hyphen is part of the word, not a separator.  Both
        # of these inverted to nonsense before that distinction existed.
        ("HORSE-CHESTNUT", "horse chestnut"),
        ("MOUNTAIN-ASH", "mountain ash"),
        ("HORNBEAM - BLUE-BEECH", "blue beech hornbeam"),
        # A single comma inverts, the way normalize_tree_name reads one.
        ("Spruce, Colorado", "colorado spruce"),
        # A quoted cultivar comes off, so one entry covers every selection.
        ("JAPANESE TREE LILAC 'IVORY SILK'", "japanese tree lilac"),
        ("Basswood 'Redmond'", "basswood"),
        # An apostrophe inside a word is not a cultivar quote.
        ("SARGENT'S CHERRY", "sargent s cherry"),
        # Rank words carry nothing once the value is a key.
        ("ASH SPP.", "ash"),
        ("Acer species", "acer"),
        # Punctuation and accents go.
        ("SHRUB / HEDGE", "shrub hedge"),
        ("Érable", "erable"),
        (None, ""),
        ("   ", ""),
    ],
)
def test_common_name_key(raw, expected):
    assert common_name_key(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("MAPLE - NORWAY", "Acer platanoides"),
        ("NORWAY MAPLE", "Acer platanoides"),
        ("Norway Maple", "Acer platanoides"),
        ("HONEY LOCUST 'SKYLINE'", "Gleditsia triacanthos"),
        # A genus is an answer.
        ("ASH SPP.", "Fraxinus"),
        ("SERVICEBERRY", "Amelanchier"),
        # A growth form keeps its form rather than falling to Unknown, and an
        # empty site is handed back so enforce_tree_schema drops the row.
        ("SHRUB / HEDGE", "SHRUB / HEDGE"),
        ("STUMP", "STUMP"),
        # Unresolved is None -- never the raw value, which sanitize_species
        # would sometimes accept as an invented genus.
        ("TO BE UPDATED", None),
        ("Katsura tree unknown thing", None),
        ("", None),
        (None, None),
    ],
)
def test_species_from_common_name(raw, expected):
    assert species_from_common_name(raw) == expected


def test_an_unresolved_single_word_is_not_published_as_a_genus():
    """The failure this module exists to avoid.

    "Hackberry" is a single capitalised word, so `sanitize_species` cannot
    tell it from a genus and would keep it -- and every tree carrying it would
    then join to an enrichment row describing a plant that does not exist.
    The table resolves it; a name the table does *not* have must come back
    `None` rather than being passed through.
    """
    assert species_from_common_name("HACKBERRY") == "Celtis occidentalis"
    invented = "Woodwardia"  # genus-shaped, not a tree common name
    assert sanitize_species(invented) == invented
    assert species_from_common_name(invented) is None
