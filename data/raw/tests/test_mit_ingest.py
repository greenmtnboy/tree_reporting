"""MIT's campus tree layer: the id, the removed-tree filter, the cultivar,
diameter and date readings, and Cambridge dropping its copy of MIT's rows,
pinned without a network fetch."""

from datetime import date
from pathlib import Path
import importlib.util
import sys

RAW_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAW_DIR))


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mit = _load("mit_tree_info", RAW_DIR / "uscam" / "mit_tree_info.py")
cam = _load("cambridge_tree_info_for_mit", RAW_DIR / "uscam" / "cambridge_tree_info.py")


def _feature(species="Gleditsia triacanthos", *, tree_id="1101", active="1",
             add_taxon=" ", planted=" ", dbhs=("12.00",), x=-71.0921, y=42.3595):
    attributes = {
        "TreeID": tree_id,
        "ScientName": species,
        "AddTaxon": add_taxon,
        "Active": active,
        "TreeDatePl": planted,
    }
    for i, field in enumerate(mit.DBH_FIELDS):
        attributes[field] = dbhs[i] if i < len(dbhs) else "0"
    return {"attributes": attributes, "geometry": {"x": x, "y": y}}


class TestTreeId:
    def test_the_grounds_team_number_is_prefixed(self):
        assert mit.tree_id_for("1101") == "mit-1101"
        assert mit.tree_id_for(" 1101 ") == "mit-1101"

    def test_a_missing_id_stays_none(self):
        """None, so enforce_tree_schema refuses the build instead of inventing one."""
        assert mit.tree_id_for(None) is None
        assert mit.tree_id_for(" ") is None


class TestStanding:
    def test_an_inactive_tree_is_gone(self):
        assert not mit.is_standing(_feature(active="0"))

    def test_active_dead_and_marked_for_removal_stay(self):
        """`Removal = 'Remove'` is a recommendation and a dead tree is still a
        tree; only `Active = '0'` says it was taken out."""
        assert mit.is_standing(_feature(active="1"))
        assert mit.is_standing(_feature(active=" "))


class TestCultivar:
    def test_a_capitalised_name_is_the_cultivar(self):
        assert mit.cultivar_for("Princeton") == "Princeton"
        assert mit.cultivar_for("'Princeton'") == "Princeton"
        assert mit.cultivar_for("Ivory Silk") == "Ivory Silk"

    def test_a_description_is_not(self):
        for raw in ("fastigiate", "variegated", "Varigated", "var. japonica ?Prospector?",
                    "x warei 'long'", " ", None):
            assert mit.cultivar_for(raw) is None, raw


class TestDiameter:
    def test_one_stem(self):
        assert mit.dbh_for(_feature(dbhs=("12.00",))["attributes"]) == 12.0

    def test_stems_combine_by_basal_area(self):
        assert mit.dbh_for(_feature(dbhs=("3.00", "4.00"))["attributes"]) == 5.0

    def test_blank_and_zero_are_no_measurement(self):
        assert mit.dbh_for(_feature(dbhs=(" ", "0"))["attributes"]) is None


class TestTransform:
    def test_row_mapping(self):
        row = mit.transform(
            [_feature(add_taxon="Princeton", planted="10/01/2024", species="Ulmus americana")]
        ).to_pylist()[0]
        assert row["tree_id"] == "mit-1101"
        assert row["city"] == "USCAM"
        assert row["species"] == "Ulmus americana"
        assert row["cultivar"] == "Princeton"
        assert row["plant_date"] == date(2024, 10, 1)
        assert (row["latitude"], row["longitude"]) == (42.3595, -71.0921)
        assert row["diameter_at_breast_height"] == 12.0
        assert "tree_name" not in row

    def test_an_unparseable_planting_is_no_date(self):
        row = mit.transform([_feature(planted="Fall 2023")]).to_pylist()[0]
        assert row["plant_date"] is None


class TestCambridgeDropsItsCopy:
    def test_mit_owned_rows_are_dropped(self):
        records = [
            {"treeid": "M1540385", "ownership": "MIT"},
            {"treeid": "12345", "ownership": "City"},
            {"treeid": "H1544191", "ownership": "Harvard"},
            {"treeid": "23456"},
        ]
        kept, dropped = cam.drop_mit_campus_rows(records)
        assert dropped == 1
        assert [r["treeid"] for r in kept] == ["12345", "H1544191", "23456"]

    def test_the_feed_is_asked_for_ownership(self):
        assert "ownership" in cam.SELECT.split(",")
