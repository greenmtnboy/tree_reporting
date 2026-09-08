"""`_japanese_species`, the Japanese vernacular name -> binomial table.

The table is curated by hand, so what a test can add is the mechanical half:
that every value is a name the ingest would keep as written, that every key is
in normalised form (a key that is not can never be looked up), and that the
normaliser folds the four things Tokyo's two files actually publish.

The taxonomy itself is not testable here and is not meant to be -- it is
reviewed by reading the file, the same way `COMMON_NAME_SPECIES`,
`SPECIES_SYNONYMS` and `_NON_TAXON_REWRITES` are.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

RAW_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAW_DIR))

from _ingest_shared import (  # noqa: E402
    SPECIES_SENTINELS,
    sanitize_species,
)
from _japanese_species import (  # noqa: E402
    JAPANESE_SPECIES,
    japanese_name_key,
    species_from_japanese_name,
)


@pytest.mark.parametrize("key,value", sorted(JAPANESE_SPECIES.items()))
def test_every_value_is_a_name_the_ingest_keeps(key: str, value: str):
    """`sanitize_species` must return the value unchanged.

    `species` is the join key into the enrichment table, and
    `enforce_tree_schema` puts every value through `sanitize_species` before
    publishing it.  A value that gets rewritten there (a rank below species, a
    U+00D7 hybrid mark, a lowercase genus) would publish as something other
    than what this table says, and a value that gets *rejected* would publish
    as `Unknown` -- so the table would look right and do nothing.
    """
    assert sanitize_species(value) == value, (
        f"{key!r} -> {value!r} is not a name sanitize_species keeps as written; "
        f"it emits {sanitize_species(value)!r}"
    )


@pytest.mark.parametrize("key", sorted(JAPANESE_SPECIES))
def test_every_key_is_in_normalised_form(key: str):
    """A key not in `japanese_name_key` form is dead: nothing can match it."""
    assert japanese_name_key(key) == key


def test_no_value_is_also_a_key():
    """One lookup is enough -- the table is not chained.

    The same rule `SPECIES_SYNONYMS` and `COMMON_NAME_SPECIES` follow.  A key
    and a value cannot collide in practice here, since the keys are Japanese
    and the values are Latin, and that is exactly why it is worth asserting
    rather than assuming: a future key transliterated into romaji would.
    """
    assert not set(JAPANESE_SPECIES) & set(JAPANESE_SPECIES.values())


def test_no_value_is_a_sentinel():
    """A sentinel is not a taxon and must not be reachable from this table.

    `species_from_japanese_name` returns a sentinel by asking
    `form_sentinel_for`, which is the one place that decision lives.  A
    sentinel hardcoded here would be a second, silent copy of it.
    """
    assert not set(JAPANESE_SPECIES.values()) & SPECIES_SENTINELS


@pytest.mark.parametrize(
    "raw,expected",
    [
        # Katakana, the form almost every value takes, passes through.
        ("イチョウ", "イチョウ"),
        # Hiragana folds to katakana: the same name, typed without switching
        # input mode.  Both spellings are published.
        ("さくら", "サクラ"),
        ("しらかし", "シラカシ"),
        # Every kind of space goes, including the ideographic one.
        ("ロドレイア　ヘンリー", "ロドレイアヘンリー"),
        ("タイサンボク リトルジェム", "タイサンボクリトルジェム"),
        # The katakana middle dot separates a transliterated binomial.
        ("ユッカ・エレファンティペス", "ユッカエレファンティペス"),
        # NFKC folds half-width katakana onto full-width.
        ("ｲﾁｮｳ", "イチョウ"),
        (None, ""),
        ("   ", ""),
    ],
)
def test_japanese_name_key(raw, expected):
    assert japanese_name_key(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("イチョウ", "Ginkgo biloba"),
        ("ケヤキ", "Zelkova serrata"),
        # A genus is an answer: the survey recorded "cherry", not a species.
        ("サクラ", "Prunus"),
        ("サクラ類", "Prunus"),
        ("モクレン属", "Magnolia"),
        # A hiragana spelling reaches the same row.
        ("さくら", "Prunus"),
        # A growth form keeps its form rather than falling to Unknown; the
        # sentinel comes from form_sentinel_for, so this returns the raw value
        # for enforce_tree_schema to map.
        ("枯木", "枯木"),
        ("ヤシ科sp.", "ヤシ科sp."),
        # Unresolved is None -- never the raw value.
        ("不明", None),
        ("ツバキ科", None),
        ("カナメモチ/ハマヒサカキ", None),
        ("モクレンモドキ", None),
        ("", None),
        (None, None),
    ],
)
def test_species_from_japanese_name(raw, expected):
    assert species_from_japanese_name(raw) == expected


def test_the_two_names_gbif_got_wrong_are_curated():
    """The reason this table is curated rather than generated.

    GBIF's Japanese vernacular index drafted 290 of the 446 values and is
    right almost everywhere, which is what makes it useful and what makes its
    misses dangerous.  These two would have mislabelled thousands of trees,
    and each is pinned because the fix is a single table row that a
    regeneration would silently undo.
    """
    # `ツバキ` is the common camellia.  GBIF answers *Camellia hiemalis*,
    # which is `カンツバキ` -- published here as its own value, 966 times.
    assert species_from_japanese_name("ツバキ") == "Camellia japonica"
    assert species_from_japanese_name("カンツバキ") == "Camellia hiemalis"
    # `アメリカヒイラギ` is "American holly".  GBIF answers *Cartrema
    # americana*, the devilwood.
    assert species_from_japanese_name("アメリカヒイラギ") == "Ilex opaca"
    # ... and the other hollies this data publishes alongside it, which is
    # what makes the holly reading the right one.
    assert species_from_japanese_name("セイヨウヒイラギ") == "Ilex aquifolium"
    assert species_from_japanese_name("シナヒイラギ") == "Ilex cornuta"


def test_an_unresolved_name_is_not_published_as_a_genus():
    """The failure this module exists to avoid, in its Japanese form.

    `_common_name_species` refuses to pass an unresolved English name through
    because a single capitalised word is indistinguishable from a genus.  Here
    the raw value could never survive `sanitize_species` anyway, so the point
    is the *other* half of that rule: a name this table does not have must
    come back `None` rather than being guessed at from a prefix it shares with
    one that it does.
    """
    assert species_from_japanese_name("イチョウ") == "Ginkgo biloba"
    # Same first three characters, not a name in the table.
    assert species_from_japanese_name("イチョウモドキ") is None
