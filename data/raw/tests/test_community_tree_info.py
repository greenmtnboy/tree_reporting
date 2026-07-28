from pathlib import Path
import importlib.util
import sys

import pyarrow as pa

RAW_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAW_DIR))
SPEC = importlib.util.spec_from_file_location(
    "community_tree_info", RAW_DIR / "community_tree_info.py"
)
assert SPEC and SPEC.loader
community = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(community)


def test_records_to_table_emits_canonical_city_rows():
    table = community.records_to_table(
        [
            {
                "treeId": "community-abc",
                "city": "ussfo",
                "species": "platanus X HISPANICA",
                "latitude": 37.77,
                "longitude": -122.42,
            }
        ]
    )

    assert table.to_pylist() == [
        {
            "tree_id": "community-abc",
            "city": "USSFO",
            "species": "Platanus x hispanica",
            "tree_name": "Platanus x hispanica",
            "plant_date": None,
            "diameter_at_breast_height": None,
            "latitude": 37.77,
            "longitude": -122.42,
        }
    ]
    assert table.schema.field("plant_date").type == pa.date32()
    assert table.schema.field("latitude").type == pa.float64()


def test_records_to_table_skips_unknown_cities_and_bad_coordinates():
    table = community.records_to_table(
        [
            {
                "treeId": "community-unknown",
                "city": "XXXXX",
                "latitude": 1,
                "longitude": 2,
            },
            {
                "treeId": "community-outside",
                "city": "USSFO",
                "latitude": 0,
                "longitude": 0,
            },
        ]
    )

    assert table.num_rows == 0
    assert table.schema.field("species").type == pa.string()


def test_records_to_table_keeps_unidentified_approved_trees():
    table = community.records_to_table(
        [
            {
                "treeId": "community-unidentified",
                "city": "USNYC",
                "species": None,
                "latitude": 40.72,
                "longitude": -74.0,
            }
        ]
    )

    assert table.column("species").to_pylist() == ["Unknown"]
