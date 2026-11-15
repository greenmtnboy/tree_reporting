"""Northeastern University's campus Arboretum layer: the id, the non-woody
filter and the row mapping, pinned without a network fetch."""

from pathlib import Path
import importlib.util
import sys

RAW_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAW_DIR))

_spec = importlib.util.spec_from_file_location(
    "northeastern_tree_info", RAW_DIR / "usbos" / "northeastern_tree_info.py"
)
assert _spec and _spec.loader
neu = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(neu)


def _feature(species, *, global_id="E0E77880-2823-4372-A286-C519D37195B0",
             x=-71.09234, y=42.338003, diameter=None):
    return {
        "attributes": {
            "GlobalID": global_id,
            "SpeciesType": species,
            "Diameter": diameter,
        },
        "geometry": {"x": x, "y": y},
    }


class TestTreeId:
    def test_global_id_is_unbraced_and_lowered(self):
        assert neu.tree_id_for("{E0E77880-2823-4372-A286-C519D37195B0}") == (
            "neu-e0e77880-2823-4372-a286-c519d37195b0"
        )

    def test_a_missing_id_stays_none(self):
        """None, so enforce_tree_schema refuses the build instead of inventing one."""
        assert neu.tree_id_for(None) is None
        assert neu.tree_id_for("") is None


class TestWoodyFilter:
    def test_ferns_and_groundcovers_are_dropped(self):
        for species in ("Adiantum pedatum", "Asarum canadense",
                        "Epimedium sp. nov. 'Spine Tingler'", "Thymus serpyllum 'Elfin'"):
            assert not neu.is_woody(_feature(species))

    def test_trees_shrubs_and_blanks_stay(self):
        for species in ("Acer rubrum", "Hydrangea paniculata 'Bobo'", "Unknown", None):
            assert neu.is_woody(_feature(species))


class TestTransform:
    def test_row_mapping(self):
        row = neu.transform(
            [_feature("Acer rubrum")]
        ).to_pylist()[0]
        assert row["tree_id"] == "neu-e0e77880-2823-4372-a286-c519d37195b0"
        assert row["city"] == "USBOS"
        assert row["species"] == "Acer rubrum"
        assert row["latitude"] == 42.338003
        assert row["longitude"] == -71.09234
        assert row["plant_date"] is None
        assert "tree_name" not in row
        assert row["diameter_at_breast_height"] is None

    def test_a_zero_diameter_is_no_measurement(self):
        row = neu.transform([_feature("Acer rubrum", diameter=0)]).to_pylist()[0]
        assert row["diameter_at_breast_height"] is None
