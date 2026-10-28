"""TreeKeeper labels (`common (Scientific)`, genus-first common names) and the
planting-site rows that are not trees, for both towns read through
`shared.platforms.treekeeper`.  Pinned without a network fetch."""

import importlib.util
import sys
from pathlib import Path

RAW_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAW_DIR))

from shared.platforms.treekeeper import split_label, tree_name_for  # noqa: E402


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, RAW_DIR / relative)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


somerville = _load("somerville_tree_info", "usbos/somerville_tree_info.py")
belmont = _load("belmont_tree_info", "usbos/belmont_tree_info.py")


class TestLabels:
    def test_common_and_scientific_split(self):
        assert split_label("maple: Norway (Acer platanoides)") == (
            "maple: Norway",
            "Acer platanoides",
        )

    def test_the_last_top_level_group_is_the_scientific_name(self):
        assert split_label("Vacant (Do Not Plant) (Vacant (Do-Not-Plant))") == (
            "Vacant (Do Not Plant)",
            "Vacant (Do-Not-Plant)",
        )

    def test_a_cultivar_inside_the_group(self):
        assert split_label("Maple, Norway 'Crimson King' (Acer platanoides 'Crimson King')") == (
            "Maple, Norway 'Crimson King'",
            "Acer platanoides 'Crimson King'",
        )

    def test_a_missing_label(self):
        assert split_label(None) == (None, None)

    def test_colon_inversion_is_undone(self):
        assert tree_name_for("maple: Norway") == "Norway maple"

    def test_comma_inversion_is_undone(self):
        assert tree_name_for("Maple, Red") == "Red maple"

    def test_spp_is_not_a_qualifier(self):
        assert tree_name_for("cherry/plum: spp.") == "Cherry/plum"


def _row(module, site_id, label, dbh=4.0):
    return {
        "SITE_ID": site_id,
        module.SPECIES: label,
        "LATITUDE": 42.39,
        "LONGITUDE": -71.15,
        module.DBH_INCHES: dbh,
    }


class TestSomerville:
    def test_sites_without_a_tree_are_dropped(self):
        table, dropped = somerville.build_table(
            [
                _row(somerville, 1, "maple: red (Acer rubrum)"),
                _row(somerville, 2, "stump (Stump)"),
                _row(somerville, 3, "vacant site: large (Vacant site-large)"),
                _row(somerville, 4, "Vacant (Do Not Plant) (Vacant (Do-Not-Plant))"),
                _row(somerville, 5, None),
            ]
        )
        assert table.column("tree_id").to_pylist() == ["som-1"]
        assert dropped == {"no species": 1, "not a tree": 3}


class TestBelmont:
    def test_sites_without_a_tree_are_dropped(self):
        table, dropped = belmont.build_table(
            [
                _row(belmont, 1, "Maple, Red (Acer rubrum)"),
                _row(belmont, 2, "Stump (Stump)", dbh=0),
                _row(belmont, 3, "Vacant site small (Vacant site small)", dbh=0),
                _row(belmont, 4, "Vacant Site - Retired (Vacant Site - Retired)", dbh=0),
                _row(belmont, 5, None),
            ]
        )
        assert table.column("tree_id").to_pylist() == ["bel-1"]
        assert table.column("tree_name").to_pylist() == ["Red maple"]
        assert dropped == {"no species": 1, "not a tree": 3}

    def test_a_zero_diameter_is_unmeasured(self):
        table, _ = belmont.build_table(
            [_row(belmont, 1, "Oak, Pin (Quercus palustris)", dbh=0),
             _row(belmont, 2, "Oak, Pin (Quercus palustris)", dbh=12)]
        )
        assert table.column("diameter_at_breast_height").to_pylist() == [None, 12.0]
