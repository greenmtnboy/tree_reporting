"""A town carved out of a metro: what follows it and what stays behind.

Cambridge, Somerville, Brookline, Belmont and Watertown were partitions of
Boston's model and are cities of their own.  Their territories are polygons
(tests/test_city_territory.py); these tests are the consequences for rows:
an inventory is clipped to its town, and a row that carries no municipal
attribution -- a community submission, a reviewed detection, a bulk
prediction -- goes to the town it stands in whatever city it was recorded
under, because every such row of theirs was recorded as Boston's.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

RAW_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAW_DIR))

from shared.canopy import height_staging_name  # noqa: E402
from shared.ingest import (  # noqa: E402
    COMMUNITY_DATA_SOURCES,
    MUNICIPAL_DATA_SOURCES,
    PREDICTED_DATA_SOURCES,
    PREDICTED_STAGED_WITH,
    SATELLITE_DATA_SOURCES,
    carved_out_of,
    territory_family,
    clip_to_polygon_territory,
    validate_coordinates,
)

TOWNS = ("USBEL", "USBKL", "USCAM", "USSOM", "USWAT")
# Towns added after Boston was clipped to its own line: no partition of
# Boston's ever, but ground its metro run and reviews covered.
METRO_TOWNS = (
    "USARL", "USDED", "USLYN", "USMBH", "USMED", "USMIL", "USMLD", "USNEE",
    "USNWT", "USPEA", "USQCY", "USREV", "USSAL", "USSAU", "USSWA", "USWLT",
)

HARVARD_SQUARE = (42.3736, -71.1190)  # Cambridge
DAVIS_SQUARE = (42.3967, -71.1223)  # Somerville
COOLIDGE_CORNER = (42.3420, -71.1212)  # Brookline
BOSTON_COMMON = (42.3550, -71.0656)  # Boston
EVERETT = (42.4084, -71.0537)  # inside the metro run, no city of its own yet


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, RAW_DIR / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


community = load("community_tree_info")
satellite = load("satellite_tree_info")
predicted = load("predicted_tree_info")
community_update_time = load("community_update_time")
satellite_update_time = load("satellite_update_time")
predicted_update_time = load("predicted_update_time")


def points(*places) -> pa.Table:
    return pa.table(
        {
            "tree_id": [f"t{i}" for i in range(len(places))],
            "latitude": [lat for lat, _ in places],
            "longitude": [lon for _, lon in places],
        }
    )


# --- the split itself ------------------------------------------------------


def test_the_towns_are_cities_and_boston_no_longer_claims_them():
    # Boston is its own polygon, so nothing is carved: the towns are its
    # neighbours in one tessellation, and the family still holds them.
    assert carved_out_of("USBOS") == ()
    assert set(TOWNS) < set(territory_family("USBOS"))
    assert MUNICIPAL_DATA_SOURCES["USBOS"] == ("CITY_OF_BOSTON", "ARNOLD_ARBORETUM", "NORTHEASTERN")
    labels = {code: MUNICIPAL_DATA_SOURCES[code] for code in TOWNS}
    # The labels the rows were always published under: a split must not rename
    # a `data_source` value that dashboards, the reviewer and links filter on.
    assert labels == {
        "USBEL": ("BELMONT",),
        "USBKL": ("BROOKLINE",),
        "USCAM": ("CAMBRIDGE", "MIT"),
        "USSOM": ("SOMERVILLE",),
        "USWAT": ("WATERTOWN",),
    }


@pytest.mark.parametrize("code", TOWNS + METRO_TOWNS)
def test_a_town_takes_every_class_of_row_boston_published_there(code: str):
    """Boston publishes satellite and predicted rows; ground carved out of it
    must keep publishing them, or the split deletes trees from the map."""
    assert code in COMMUNITY_DATA_SOURCES
    assert SATELLITE_DATA_SOURCES[code] == f"SATELLITE_{code}"
    assert PREDICTED_DATA_SOURCES[code] == f"PREDICTED_{code}"
    assert PREDICTED_STAGED_WITH[code] == "USBOS"


# --- inventories are clipped to a polygon territory -------------------------


def test_an_inventory_row_in_the_next_town_is_dropped(capsys):
    kept = clip_to_polygon_territory(points(HARVARD_SQUARE, DAVIS_SQUARE), city="Cambridge", city_code="USCAM")
    assert kept["tree_id"].to_pylist() == ["t0"]
    assert "dropped 1 of 2 rows that stand outside USCAM's boundary" in capsys.readouterr().err


def test_boston_keeps_only_the_rows_inside_its_own_line():
    kept = clip_to_polygon_territory(points(BOSTON_COMMON, COOLIDGE_CORNER, EVERETT), city_code="USBOS")
    assert kept["tree_id"].to_pylist() == ["t0"]


def test_a_city_of_rectangles_keeps_what_its_portal_attributes_to_it():
    """A staircase is too coarse to overrule the publisher: no clip."""
    table = points((37.77, -122.42), (37.80, -122.27))  # San Francisco, Oakland
    assert clip_to_polygon_territory(table, city_code="USSFO") is table


def test_the_clip_runs_inside_the_coordinate_check_every_ingest_calls():
    table = validate_coordinates(points(HARVARD_SQUARE, DAVIS_SQUARE, BOSTON_COMMON), city_code="USSOM")
    assert table["tree_id"].to_pylist() == ["t1"]


# --- unattributed rows go to the town they stand in -------------------------


def community_record(city: str, place, tree_id: str = "community-1") -> dict:
    return {"treeId": tree_id, "city": city, "species": "Acer rubrum", "latitude": place[0], "longitude": place[1]}


def test_a_submission_recorded_as_boston_publishes_in_the_town_it_stands_in():
    rows = community.records_to_table(
        [
            community_record("USBOS", HARVARD_SQUARE, "a"),  # recorded before the split
            community_record("USCAM", DAVIS_SQUARE, "b"),  # the nearest centre was Cambridge's
            community_record("USBOS", BOSTON_COMMON, "c"),
        ]
    ).to_pylist()
    assert [(r["tree_id"], r["city"], r["data_source"]) for r in rows] == [
        ("a", "USCAM", "COMMUNITY_USCAM"),
        ("b", "USSOM", "COMMUNITY_USSOM"),
        ("c", "USBOS", "COMMUNITY_USBOS"),
    ]


def test_a_submission_outside_its_family_is_still_dropped(capsys):
    table = community.records_to_table([community_record("USSFO", HARVARD_SQUARE)])
    assert table.num_rows == 0
    assert "coordinates outside USSFO" in capsys.readouterr().err


def test_a_reviewed_detection_follows_the_same_rule():
    record = {
        "schemaVersion": 1, "treeId": "sat-1", "city": "USBOS", "species": None,
        "latitude": COOLIDGE_CORNER[0], "longitude": COOLIDGE_CORNER[1],
        "publishedAt": "2026-09-11T12:00:00.000Z",
    }
    (row,) = satellite.records_to_table([record]).to_pylist()
    assert (row["city"], row["data_source"]) == ("USBKL", "SATELLITE_USBKL")


def prediction(tree_id: str, place) -> dict:
    return {
        "tree_id": tree_id, "city": "USBOS", "latitude": place[0], "longitude": place[1],
        "genus": "Acer", "species": "Acer rubrum", "score": 0.4, "crown_width_m": 5.5,
        "run_id": "run", "imagery_version": "naip2023",
        "species_confidence": 0.85, "genus_confidence": 0.9,
    }


METRO_RUN = [
    prediction("pred-cam", HARVARD_SQUARE),
    prediction("pred-som", DAVIS_SQUARE),
    prediction("pred-bos", BOSTON_COMMON),
    prediction("pred-ring", EVERETT),
]


def test_a_town_reads_the_metro_runs_staged_file():
    assert predicted.staging_name("USCAM") == predicted.staging_name("USBOS") == "usbos_predicted_staging.parquet"
    assert predicted.staging_name("USSFO") == "ussfo_predicted_staging.parquet"


def test_each_prediction_of_the_metro_run_publishes_in_exactly_one_city(tmp_path, monkeypatch):
    path = tmp_path / "usbos.parquet"
    pq.write_table(pa.Table.from_pylist(METRO_RUN), path)
    monkeypatch.setenv("PREDICTED_STAGING_URL", str(tmp_path / "{code}.parquet"))

    published: dict[str, str] = {}
    for code in ("USBOS", *TOWNS, *METRO_TOWNS):
        for table in predicted.city_tables(code):
            for row in table.to_pylist():
                assert row["tree_id"] not in published, f"{row['tree_id']} published by two cities"
                assert row["data_source"] == f"PREDICTED_{code}" and row["city"] == code
                published[row["tree_id"]] = code
    assert published == {"pred-cam": "USCAM", "pred-som": "USSOM", "pred-bos": "USBOS"}, (
        "a prediction in a town that is not yet a city (Everett) publishes nowhere"
    )


def test_rows_far_from_a_town_are_dropped_before_they_become_records():
    """A town keeps a few rows in a hundred of the metro file."""
    near, far = predicted.near_territory(pa.Table.from_pylist(METRO_RUN), "USCAM")
    assert far == 1 and set(near["tree_id"].to_pylist()) == {"pred-cam", "pred-som", "pred-bos"}
    # Davis Square and Boston Common are inside Cambridge's envelope and
    # outside its polygon: the row loop, not the envelope, decides.
    assert predicted.records_to_table(near.to_pylist(), "USCAM")["tree_id"].to_pylist() == ["pred-cam"]


# --- freshness: a carved family moves together ------------------------------


def test_an_approval_recorded_as_boston_marks_the_towns_stale_too():
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    approved = datetime(2026, 11, 20, tzinfo=timezone.utc)
    for probe in (community_update_time, satellite_update_time):
        by_city = probe.with_family({"USBOS": approved, "USCAM": epoch, "USSOM": epoch, "USSFO": epoch})
        assert by_city == {"USBOS": approved, "USCAM": approved, "USSOM": approved, "USSFO": epoch}


def test_a_towns_predicted_watermark_is_the_metro_files(monkeypatch):
    asked: list[str] = []

    def modified_at(name: str) -> datetime:
        asked.append(name)
        return datetime(2026, 11, 21, tzinfo=timezone.utc)

    monkeypatch.setattr(predicted_update_time, "staging_modified_at", modified_at)
    by_city = predicted_update_time.fetch_modified_by_city()
    assert set(TOWNS + METRO_TOWNS) <= set(by_city)
    assert asked.count("usbos_predicted_staging.parquet") == 1 + len(TOWNS) + len(METRO_TOWNS)


def test_cambridge_reads_the_metros_canopy_table():
    assert height_staging_name("USCAM") == height_staging_name("USBOS") == "usbos_canopy_height.parquet"
