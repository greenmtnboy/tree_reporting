"""The satellite ingest: what a reviewed detection publishes, and what it never does."""

from pathlib import Path
import importlib.util
import sys

import pyarrow as pa

RAW_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAW_DIR))

from _ingest_shared import UNKNOWN_SPECIES  # noqa: E402

SPEC = importlib.util.spec_from_file_location(
    "satellite_tree_info", RAW_DIR / "satellite_tree_info.py"
)
assert SPEC and SPEC.loader
satellite = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(satellite)


def record(**overrides):
    base = {
        "schemaVersion": 1,
        "treeId": "sat-abc",
        "city": "USSFO",
        "latitude": 37.77,
        "longitude": -122.42,
        "positionRole": "crown_center",
        "species": "Platanus x hispanica",
        "speciesSource": "reviewer",
        "predictedDbhInches": 11.2,
        "measuredDbhInches": None,
        "predictedCrownWidthM": 6.1,
        "duplicateOfTreeId": None,
        "tileId": "USSFO-naip2022-abcd1234-r000005_c000073",
        "imageryVersion": "naip2022-abcd1234",
        "runId": "sf-boston-naip-curation-v5-retrain",
        "predictionId": "r000005_c000073:72:59",
        "acquisitionDate": "2022-05-19",
        "publishedAt": "2026-09-11T12:00:00.000Z",
    }
    base.update(overrides)
    return base


def test_records_to_table_emits_canonical_city_rows():
    table = satellite.records_to_table([record(species="platanus X HISPANICA")])

    assert table.to_pylist() == [
        {
            "tree_id": "sat-abc",
            "city": "USSFO",
            # The per-city satellite label is what makes this row a disjoint
            # partition that Trilogy unions into the SF Parquet.
            "data_source": "SATELLITE_USSFO",
            "species": "Platanus x hispanica",
            "tree_name": None,
            "plant_date": None,
            "diameter_at_breast_height": None,
            "latitude": 37.77,
            "longitude": -122.42,
            "submission_photo_url": None,
            "borough": None,
            "cultivar": None,
        }
    ]
    assert table.schema.field("plant_date").type == pa.date32()
    assert table.schema.field("diameter_at_breast_height").type == pa.float64()


def test_the_model_dbh_estimate_never_becomes_the_measured_column():
    """`diameter_at_breast_height` means a measurement everywhere else in the
    map, and the cluster merge would let an estimate win over a null municipal
    value.  Only a diameter a person measured is published."""
    estimated = satellite.records_to_table([record(predictedDbhInches=14.0)])
    assert estimated.column("diameter_at_breast_height").to_pylist() == [None]

    measured = satellite.records_to_table(
        [record(predictedDbhInches=14.0, measuredDbhInches=12.5)]
    )
    assert measured.column("diameter_at_breast_height").to_pylist() == [12.5]

    # A zero or negative "measurement" is a placeholder, not a stem.
    assert satellite.records_to_table(
        [record(measuredDbhInches=0)]
    ).column("diameter_at_breast_height").to_pylist() == [None]


def test_an_unconfirmed_species_publishes_as_unknown():
    table = satellite.records_to_table([record(species=None, speciesSource=None)])
    assert table.column("species").to_pylist() == [UNKNOWN_SPECIES]


def test_records_outside_the_partition_are_skipped():
    table = satellite.records_to_table(
        [
            # A city imagery has not been run over has no satellite partition,
            # so a row for it would be dropped by the planner anyway; drop it
            # here where the message says why.
            record(treeId="sat-paris", city="FRPAR", latitude=48.85, longitude=2.35),
            record(treeId="sat-sea", city="USSFO", latitude=0, longitude=0),
            record(treeId="sat-v2", schemaVersion=2),
            record(treeId="", city="USSFO"),
        ]
    )
    assert table.num_rows == 0
    assert table.schema.field("species").type == pa.string()


def test_a_linked_duplicate_is_published_where_it_was_exported():
    """The reviewer exports a linked detection at the inventory tree's own
    coordinates; the ingest publishes that position as given, which is what
    guarantees the grid merge puts the two rows in one cluster."""
    table = satellite.records_to_table(
        [
            record(
                treeId="sat-linked",
                positionRole="linked_trunk",
                duplicateOfTreeId="sf-123",
                latitude=37.7701,
                longitude=-122.4201,
            )
        ]
    )
    row = table.to_pylist()[0]
    assert (row["latitude"], row["longitude"]) == (37.7701, -122.4201)
    assert row["data_source"] == "SATELLITE_USSFO"


def test_main_filters_records_to_the_pushed_city(monkeypatch):
    records = [
        record(treeId="a", city="USBOS", latitude=42.36, longitude=-71.06),
        record(treeId="b", city="USSFO"),
    ]
    monkeypatch.setattr(satellite, "load_published_records", lambda: records)
    emitted = {}
    monkeypatch.setattr(satellite, "emit", lambda t: emitted.update(table=t))

    satellite.main(["--filter", "city=usbos"])
    assert emitted["table"].column("city").to_pylist() == ["USBOS"]

    satellite.main([])
    assert sorted(emitted["table"].column("city").to_pylist()) == ["USBOS", "USSFO"]


def test_probe_defaults_every_wired_city_to_the_epoch(monkeypatch):
    from satellite_update_time import (
        EMPTY_DATASET_TIMESTAMP,
        column_for,
        fetch_published_at_by_city,
    )
    import satellite_update_time

    class Missing:
        status_code = 404

        def raise_for_status(self):
            raise AssertionError("not reached")

    monkeypatch.setattr(satellite_update_time.requests, "get", lambda *a, **k: Missing())
    by_city = fetch_published_at_by_city()
    assert set(by_city) == set(satellite.SATELLITE_DATA_SOURCES)
    assert set(by_city.values()) == {EMPTY_DATASET_TIMESTAMP}
    assert column_for("USSFO") == "ussfo_satellite_data_updated_through"
