"""An empty Overpass answer: refused for a mapped city, staged empty for one
recorded in OSM_UNMAPPED_CITIES.  Pinned without a network fetch."""

import sys
from pathlib import Path

import pytest

RAW_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAW_DIR))

from shared import osm  # noqa: E402
from shared.ingest import CITY_TERRITORY, OSM_DATA_SOURCES  # noqa: E402


@pytest.fixture
def empty_overpass(monkeypatch):
    monkeypatch.setattr(osm, "post_json_with_retry", lambda *a, **k: {"elements": []})


def test_an_empty_answer_is_refused_for_a_mapped_city(empty_overpass):
    with pytest.raises(RuntimeError, match="no tree nodes"):
        osm.fetch_osm_trees("USSFO")


def test_an_unmapped_city_stages_an_empty_typed_table(empty_overpass):
    code = next(iter(osm.OSM_UNMAPPED_CITIES))
    table = osm.build_table(osm.fetch_osm_trees(code), code, f"{code} OSM")
    assert table.num_rows == 0
    assert str(table.schema.field("plant_date").type) == "date32[day]"
    assert str(table.schema.field("latitude").type) == "double"


def test_every_unmapped_city_is_a_wired_osm_city():
    for code in osm.OSM_UNMAPPED_CITIES:
        assert code in OSM_DATA_SOURCES and code in CITY_TERRITORY
