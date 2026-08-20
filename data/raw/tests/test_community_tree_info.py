from pathlib import Path
import importlib.util
import sys

import pyarrow as pa

RAW_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAW_DIR))

from _ingest_shared import UNKNOWN_SPECIES  # noqa: E402
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
                "photoUrl": "https://storage.googleapis.com/pub/community/photos/x.jpg",
            }
        ]
    )

    assert table.to_pylist() == [
        {
            "tree_id": "community-abc",
            "city": "USSFO",
            # The per-city community label is what makes this row a disjoint
            # partition that Trilogy will union into the SF Parquet.
            "data_source": "COMMUNITY_USSFO",
            "species": "Platanus x hispanica",
            "tree_name": "Platanus x hispanica",
            "plant_date": None,
            "diameter_at_breast_height": None,
            "latitude": 37.77,
            "longitude": -122.42,
            "submission_photo_url": "https://storage.googleapis.com/pub/community/photos/x.jpg",
            "borough": None,
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

    # An unidentified submission still publishes; enforce_tree_schema gives it
    # the shared UNKNOWN_SPECIES sentinel rather than a null, and
    # SKIP_SPECIES keeps that sentinel out of enrichment.
    assert table.column("species").to_pylist() == [UNKNOWN_SPECIES]


# ---------------------------------------------------------------------------
# Pushdown filters
# ---------------------------------------------------------------------------

def test_parse_pushdown_filters_reads_key_value_pairs():
    assert community.parse_pushdown_filters(["--filter", "city=USTEM"]) == {
        "city": "USTEM"
    }


def test_parse_pushdown_filters_ignores_noise():
    """An unrecognised or malformed filter must not raise: the SQL predicate
    Trilogy generates alongside it still filters the rows."""
    assert community.parse_pushdown_filters([]) == {}
    assert community.parse_pushdown_filters(["--filter"]) == {}
    assert community.parse_pushdown_filters(["--filter", "novalue"]) == {}
    assert community.parse_pushdown_filters(["--other", "x=1"]) == {}
    assert community.parse_pushdown_filters(
        ["--filter", "city=USBOS", "--filter", "unknown_key=whatever"]
    ) == {"city": "USBOS", "unknown_key": "whatever"}


def test_main_filters_records_to_the_pushed_city(monkeypatch):
    records = [
        {"treeId": "a", "city": "USBOS", "species": "Acer rubrum",
         "latitude": 42.36, "longitude": -71.06},
        {"treeId": "b", "city": "USTEM", "species": "Olea europaea",
         "latitude": 33.42, "longitude": -111.94},
    ]
    monkeypatch.setattr(community, "load_published_records", lambda: records)
    emitted = {}
    monkeypatch.setattr(community, "emit", lambda t: emitted.update(table=t))

    community.main(["--filter", "city=USTEM"])
    assert emitted["table"].column("city").to_pylist() == ["USTEM"]

    community.main([])
    assert sorted(emitted["table"].column("city").to_pylist()) == ["USBOS", "USTEM"]


def test_main_city_filter_is_case_insensitive(monkeypatch):
    records = [{"treeId": "a", "city": "usbos", "species": "Acer rubrum",
                "latitude": 42.36, "longitude": -71.06}]
    monkeypatch.setattr(community, "load_published_records", lambda: records)
    emitted = {}
    monkeypatch.setattr(community, "emit", lambda t: emitted.update(table=t))

    community.main(["--filter", "city=USBOS"])
    assert emitted["table"].num_rows == 1
