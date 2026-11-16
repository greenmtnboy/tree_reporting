"""Los Angeles's Bureau of Street Services layer: species from the tooltip, and
which rows are trees.  Pinned without a network fetch."""

import importlib.util
import sys
from pathlib import Path

import pytest

RAW_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAW_DIR))

from shared.ingest import enforce_tree_schema  # noqa: E402


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, RAW_DIR / relative)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


la = _load("losangeles_tree_info", "uslax/losangeles_tree_info.py")


def _tooltip(botanical: str, location: str = "20200 SESNON BL - F35") -> str:
    # The layer stores a literal backslash-n between lines, not a newline.
    return (
        f"Tree ID: 224040\\nLocation: {location}\\n"
        f"Species: SOME COMMON NAME\\nBotanical Name: {botanical}"
    )


def _feature(botanical: str, tree_id=224040) -> dict:
    return {
        "attributes": {"TreeID": tree_id, "Type": 1, "TOOLTIP": _tooltip(botanical)},
        "geometry": {"x": -118.5749403047229, "y": 34.292475414423265},
    }


def test_tooltip_lines_parse_on_the_literal_backslash_n():
    fields = la.parse_tooltip(_tooltip("Geijera parviflora"))
    assert fields["Location"] == "20200 SESNON BL - F35"
    assert fields["Botanical Name"] == "Geijera parviflora"


@pytest.mark.parametrize(
    "botanical",
    ["Vacant - OK to plant", "VACANT/INADEQUATE SPACING", "Vacant well",
     "Stump", "STUMP", "FOR UNKNOWN STREET NAME"],
)
def test_sites_and_stumps_are_not_trees(botanical):
    assert not la.is_tree(_tooltip(botanical))


@pytest.mark.parametrize("botanical", ["Dead tree", "Not Specified", "Magnolia grandiflora"])
def test_dead_and_unspecified_trees_stay(botanical):
    assert la.is_tree(_tooltip(botanical))


@pytest.mark.parametrize(
    ("botanical", "species", "cultivar"),
    [
        ("GEIJERA PARVIFLORA", "Geijera parviflora", None),
        ("LAGERSTROEMIA I. 'ROSEA'", "Lagerstroemia indica", "Rosea"),
        ("Lagerstroemia i. 'Wat. Red'", "Lagerstroemia indica", "Wat. Red"),
        ("Not Specified", "Unknown", None),
        ("Dead Tree", "Unknown", None),
    ],
)
def test_species_and_cultivar(botanical, species, cultivar):
    table = enforce_tree_schema(
        la.transform([_feature(botanical)]), city="Los Angeles",
        data_source="LOSANGELES_OPENDATA",
    )
    assert table["species"][0].as_py() == species
    assert table["cultivar"][0].as_py() == cultivar


def test_ids_take_a_prefix_the_old_inventory_never_used():
    # The 1990s Socrata inventory's `lax-224040` is a different tree; reusing
    # its prefix would send an old deep link or report to the wrong one.
    table = la.transform([_feature("Magnolia grandiflora", tree_id=224040)])
    assert table["tree_id"][0].as_py() == "laxb-224040"
    assert table["latitude"][0].as_py() == pytest.approx(34.2924754)
