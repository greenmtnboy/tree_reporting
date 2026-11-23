"""`shared.species.korean`, the Korean vernacular name -> binomial table.

The mechanical half, as `test_japanese_species.py` does it for Tokyo: every
value is a name the ingest keeps as written, every key is in normalised form,
the table is not chained and holds no sentinel.  The taxonomy is reviewed by
reading the module.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

RAW_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAW_DIR))

from shared.ingest import (  # noqa: E402
    SPECIES_SENTINELS,
    sanitize_species,
)
from shared.species.korean import (  # noqa: E402
    KOREAN_CULTIVARS,
    KOREAN_NON_TREES,
    KOREAN_SPECIES,
    cultivar_from_korean_name,
    is_korean_non_tree,
    korean_name_key,
    species_from_korean_name,
)


@pytest.mark.parametrize("key,value", sorted(KOREAN_SPECIES.items()))
def test_every_value_is_a_name_the_ingest_keeps(key: str, value: str):
    assert sanitize_species(value) == value, (
        f"{key!r} -> {value!r} is not a name sanitize_species keeps as written; "
        f"it emits {sanitize_species(value)!r}"
    )


@pytest.mark.parametrize("key", sorted(KOREAN_SPECIES) + sorted(KOREAN_NON_TREES) + sorted(KOREAN_CULTIVARS))
def test_every_key_is_in_normalised_form(key: str):
    assert korean_name_key(key) == key


def test_no_value_is_also_a_key():
    assert not set(KOREAN_SPECIES) & set(KOREAN_SPECIES.values())


def test_no_value_is_a_sentinel():
    assert not set(KOREAN_SPECIES.values()) & SPECIES_SENTINELS


def test_a_cultivar_key_resolves_to_a_taxon():
    """A cultivar without its species would land on a row named `Unknown`."""
    assert set(KOREAN_CULTIVARS) <= set(KOREAN_SPECIES)


def test_a_non_tree_is_not_a_species():
    assert not KOREAN_NON_TREES & set(KOREAN_SPECIES)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("은행나무", "은행나무"),
        # The space in `은행나무 암나무` goes; both spellings are one key.
        ("은행나무 암나무", "은행나무암나무"),
        ("  느티나무　", "느티나무"),
        (None, ""),
        ("   ", ""),
    ],
)
def test_korean_name_key(raw, expected):
    assert korean_name_key(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("은행나무", "Ginkgo biloba"),
        ("은행나무 암나무", "Ginkgo biloba"),
        ("느티나무", "Zelkova serrata"),
        ("양버즘나무", "Platanus occidentalis"),
        # A genus is an answer: `류` is "-kind".
        ("벚나무류", "Prunus"),
        ("참나무류", "Quercus"),
        # Forms resolve to their species.
        ("반송", "Pinus densiflora"),
        ("공작단풍", "Acer palmatum"),
        # Unresolved is None -- never the raw value.
        ("기타", None),
        ("보리수", None),
        ("댕강나무", None),
        ("", None),
        (None, None),
    ],
)
def test_species_from_korean_name(raw, expected):
    assert species_from_korean_name(raw) == expected


def test_the_names_gbif_got_wrong_are_curated():
    """GBIF's Korean vernacular index drafted the table; these it got wrong."""
    # `자작나무` is the Korean white birch, not the European silver birch.
    assert species_from_korean_name("자작나무") == "Betula platyphylla"
    # `꽃사과나무` is "flowering crabapple", a category, not *Malus floribunda*.
    assert species_from_korean_name("꽃사과나무") == "Malus"
    # `목련` is "magnolia" in ordinary use, not specifically *M. kobus*.
    assert species_from_korean_name("목련") == "Magnolia"


def test_non_trees():
    assert is_korean_non_tree("공분")
    assert is_korean_non_tree("(수동)폭염그늘막")
    assert not is_korean_non_tree("은행나무")
    assert not is_korean_non_tree(None)


def test_cultivars():
    assert cultivar_from_korean_name("코니카가문비") == "Conica"
    assert cultivar_from_korean_name("은행나무") is None
