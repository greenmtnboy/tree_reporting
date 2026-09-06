"""Common names are published in sentence case, proper names kept.

`common hackberry`, `northern hackberry`, `American hackberry`, `Mississippi
hackberry`: only a genuine proper noun or proper adjective keeps its capital,
and the first word always has one.  Source casing is not preserved -- the
published table carried `Evergreen Pear`, `EVERGREEN PEAR` and `evergreen
pear` side by side -- and the same rule runs on load, on the LLM run's rows
and on a row saved in the admin form.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pyarrow as pa
import pytest

RAW_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAW_DIR))

from enrichment._common_name_style import (  # noqa: E402
    PROPER_PHRASES,
    PROPER_WORDS,
    normalize_common_name,
    normalize_common_names,
)
from enrichment._tree_shared import with_normalized_common_names  # noqa: E402


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("common hackberry", "Common hackberry"),
        ("Common Hackberry", "Common hackberry"),
        ("COMMON HACKBERRY", "Common hackberry"),
        ("Mississippi Hackberry", "Mississippi hackberry"),
        ("American Hackberry", "American hackberry"),
        ("EVERGREEN PEAR", "Evergreen pear"),
        ("Evergreen Pear", "Evergreen pear"),
        ("Chinese Evergreen Pear", "Chinese evergreen pear"),
        ("Northern White Cedar", "Northern white cedar"),
        ("Rocky Mountain Douglas-fir", "Rocky Mountain Douglas-fir"),
        ("DOUGLAS FIR", "Douglas fir"),
        ("Port Orford Cedar", "Port Orford cedar"),
        ("Eastern Red-cedar", "Eastern red-cedar"),
        ("Kentucky Coffee Tree", "Kentucky coffee tree"),
        ("LONDON PLANE", "London plane"),
        ("Chinese London Plane", "Chinese London plane"),
        ("cedar of Lebanon", "Cedar of Lebanon"),
        ("Balm Of Gilead Fir", "Balm of Gilead fir"),
        ("New Zealand cabbagetree", "New Zealand cabbagetree"),
        ("NEW ZEALAND CHRISTMAS TREE", "New Zealand Christmas tree"),
        ("Hong Kong dogwood", "Hong Kong dogwood"),
    ],
)
def test_sentence_case_with_proper_names(raw, expected):
    assert normalize_common_name(raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("shrubby St. John's wort", "Shrubby St. John's wort"),
        ("Père David's maple", "Père David's maple"),
        ("Yellowish Dodge's Hawthorn", "Yellowish Dodge's hawthorn"),
        ("Diels' Abelia", "Diels' abelia"),
        ("spotted Joe Pye weed", "Spotted Joe Pye weed"),
        ("Common Solomon's Seal", "Common Solomon's seal"),
        ("Eurasian Solomon's seal", "Eurasian Solomon's seal"),
        ("Common Dutchman's Pipe", "Common Dutchman's pipe"),
        ("Living Christmas Tree", "Living Christmas tree"),
        ("Mt. Fuji Cherry", "Mt. Fuji cherry"),
        ("Winter King Green Hawthorn", "Winter King green hawthorn"),
        ("Autumn Blaze Red Maple", "Autumn Blaze red maple"),
        ("Eddie's White Wonder Dogwood", "Eddie's White Wonder dogwood"),
    ],
)
def test_people_saints_feasts_and_cultivar_phrases_keep_their_capitals(raw, expected):
    assert normalize_common_name(raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        # Title words are common nouns unless part of a listed phrase.
        ("King Sago", "King sago"),
        ("Queen Wattle", "Queen wattle"),
        ("Lady Fern", "Lady fern"),
        ("Lady's-mantle", "Lady's-mantle"),
        ("Jack-in-the-pulpit", "Jack-in-the-pulpit"),
        ("King Billy Pine", "King Billy pine"),
        # Descriptive words never keep a capital.
        ("Weeping Willow", "Weeping willow"),
        ("Red Maple", "Red maple"),
        ("Southern Live Oak", "Southern live oak"),
        ("Mock Plane Tree", "Mock plane tree"),
        ("Deodar Cedar", "Deodar cedar"),
    ],
)
def test_descriptive_and_title_words_are_lowercased(raw, expected):
    assert normalize_common_name(raw) == expected


def test_a_quoted_cultivar_is_kept_as_written():
    assert normalize_common_name("Apple 'James Grieve'") == "Apple 'James Grieve'"
    assert normalize_common_name("Olea europaea 'Majestic Beauty'") == "Olea europaea 'Majestic Beauty'"
    assert normalize_common_name("Black Locust 'Casque Rouge'") == "Black locust 'Casque Rouge'"
    # ... unless the source shouted it.
    assert normalize_common_name("ENGLISH HAWTHORN 'CRIMSON CLOUD'") == "English hawthorn 'Crimson Cloud'"


def test_a_leading_apostrophe_is_a_letter_not_a_quote():
    """Hawaiian names write the okina as an apostrophe; it must not be read
    as the start of a cultivar."""
    assert normalize_common_name("'Ohi'a Loke") == "'Ohi'a loke"


def test_whitespace_and_blanks():
    assert normalize_common_name("  red   maple ") == "Red maple"
    assert normalize_common_name("") is None
    assert normalize_common_name("   ") is None
    assert normalize_common_name(None) is None


def test_a_non_english_name_follows_the_same_rule():
    assert normalize_common_name("laurel de la India") == "Laurel de la India"
    assert normalize_common_name("tuya del Canadá") == "Tuya del Canadá"
    assert normalize_common_name("Sophore du Japon") == "Sophore du Japon"


def test_list_normalisation_drops_case_duplicates_and_blanks():
    assert normalize_common_names(["EVERGREEN PEAR", "Evergreen Pear", "", None, "evergreen pear", "callery pear"]) == [
        "Evergreen pear",
        "Callery pear",
    ]
    assert normalize_common_names([]) is None
    assert normalize_common_names(None) is None
    assert normalize_common_names(["", "  "]) is None


def test_the_lists_are_in_the_casing_they_publish():
    """Every entry must itself be a proper name with a capital somewhere, or
    it would lowercase nothing and capitalise nothing."""
    for word in PROPER_WORDS:
        assert word[:1].isupper(), word
        assert " " not in word, f"{word!r} belongs in PROPER_PHRASES"
    for phrase in PROPER_PHRASES:
        assert any(ch.isupper() for ch in phrase), phrase
    lowered = [w.lower() for w in PROPER_WORDS]
    assert len(lowered) == len(set(lowered)), "duplicate word entries"


def test_the_table_pass_rewrites_common_names_and_reports(capsys):
    table = pa.table({
        "species": pa.array(["Pyrus kawakamii", "Acer rubrum", "Unknown"], pa.string()),
        "common_names": pa.array([["EVERGREEN PEAR", "Evergreen Pear"], ["Red maple"], None], pa.list_(pa.string())),
    })
    out = with_normalized_common_names(table)
    assert out.column("common_names").to_pylist() == [["Evergreen pear"], ["Red maple"], None]
    assert out.schema.equals(table.schema)
    assert "normalised common_names on 1 row(s)" in capsys.readouterr().err


def test_the_table_pass_is_a_no_op_on_a_clean_table():
    table = pa.table({
        "species": pa.array(["Acer rubrum"], pa.string()),
        "common_names": pa.array([["Red maple", "Swamp maple"]], pa.list_(pa.string())),
    })
    assert with_normalized_common_names(table) is table
