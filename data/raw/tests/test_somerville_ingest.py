"""Somerville's TreeKeeper labels: `common (Scientific)`, genus-first common
names, and the planting-site rows that are not trees.  Pinned without a
network fetch."""

import importlib.util
import sys
from pathlib import Path

RAW_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAW_DIR))


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, RAW_DIR / relative)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


somerville = _load("somerville_tree_info", "usbos/somerville_tree_info.py")

LAT, LON = 42.3880, -71.1100


class TestSomervilleLabels:
    def test_common_and_scientific_split(self):
        assert somerville.split_label("maple: Norway (Acer platanoides)") == (
            "maple: Norway",
            "Acer platanoides",
        )

    def test_the_last_top_level_group_is_the_scientific_name(self):
        assert somerville.split_label("Vacant (Do Not Plant) (Vacant (Do-Not-Plant))") == (
            "Vacant (Do Not Plant)",
            "Vacant (Do-Not-Plant)",
        )

    def test_a_missing_label(self):
        assert somerville.split_label(None) == (None, None)

    def test_colon_inversion_is_undone(self):
        assert somerville.tree_name_for("maple: Norway") == "Norway maple"

    def test_spp_is_not_a_qualifier(self):
        assert somerville.tree_name_for("cherry/plum: spp.") == "Cherry/plum"

    def test_sites_without_a_tree_are_dropped(self):
        def row(site_id, label):
            return {
                "SITE_ID": site_id,
                somerville.SPECIES: label,
                "LATITUDE": LAT,
                "LONGITUDE": LON,
                somerville.DBH_INCHES: 4.0,
            }

        table, dropped = somerville.build_table(
            [
                row(1, "maple: red (Acer rubrum)"),
                row(2, "stump (Stump)"),
                row(3, "vacant site: large (Vacant site-large)"),
                row(4, "Vacant (Do Not Plant) (Vacant (Do-Not-Plant))"),
                row(5, None),
            ]
        )
        assert table.column("tree_id").to_pylist() == ["som-1"]
        assert dropped == {"no species": 1, "not a tree": 3}
