"""Every city has a territory, and no two territories intersect.

An OpenStreetMap node and a community submission carry no municipal
attribution, so the city that publishes them is decided by where they fall.
`CITY_BOUNDS` cannot decide that -- it is a generous sanity box, and the
boxes of neighbouring cities overlap -- which is how the rollup came to hold
23,078 OSM trees twice, once per neighbour.  `CITY_TERRITORY` is the explicit,
non-overlapping answer, and these tests are what make "non-overlapping" a
property of the repository rather than of whoever last edited a rectangle.
"""

from __future__ import annotations

import json
import sys
from itertools import combinations
from pathlib import Path

import pytest

RAW_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = RAW_DIR.parent.parent / "src" / "src"
sys.path.insert(0, str(RAW_DIR))

from shared.ingest import (  # noqa: E402
    CITY_BOUNDS,
    CITY_TERRITORY,
    boxes_intersect,
    city_territory,
    in_city_territory,
)


def test_every_city_has_a_territory_and_nothing_else_does():
    assert set(CITY_TERRITORY) == set(CITY_BOUNDS), (
        f"missing: {sorted(set(CITY_BOUNDS) - set(CITY_TERRITORY))}; "
        f"extra: {sorted(set(CITY_TERRITORY) - set(CITY_BOUNDS))}"
    )


@pytest.mark.parametrize("code", sorted(CITY_TERRITORY))
def test_territory_rectangles_are_well_formed_and_inside_the_envelope(code: str):
    lat_min, lat_max, lon_min, lon_max = CITY_BOUNDS[code]
    boxes = city_territory(code)
    assert boxes, f"{code} has an empty territory"
    for box in boxes:
        a, b, c, d = box
        assert a < b and c < d, f"{code}: degenerate rectangle {box}"
        assert lat_min <= a and b <= lat_max and lon_min <= c and d <= lon_max, (
            f"{code}: territory rectangle {box} reaches outside CITY_BOUNDS "
            f"{CITY_BOUNDS[code]}; the envelope is the sanity box every row "
            "of the city must pass, so a territory cannot exceed it"
        )
    for x, y in combinations(boxes, 2):
        assert not boxes_intersect(x, y), f"{code}: its own rectangles overlap: {x} {y}"


def test_no_two_cities_share_ground():
    """The whole point.  A pair here is a tree the rollup publishes twice."""
    clashes = []
    for a, b in combinations(sorted(CITY_TERRITORY), 2):
        for x in CITY_TERRITORY[a]:
            for y in CITY_TERRITORY[b]:
                if boxes_intersect(x, y):
                    clashes.append((a, x, b, y))
    assert not clashes, f"territories intersect: {clashes}"


@pytest.mark.parametrize("code", sorted(CITY_TERRITORY))
def test_the_map_centre_is_in_its_own_territory(code: str):
    config = json.loads((SRC_DIR / "cityConfig.json").read_text(encoding="utf-8"))
    lon, lat = config[code]["center"]
    assert in_city_territory(code, lat, lon), (
        f"{code}: cityConfig.json centre {[lon, lat]} falls outside its territory "
        f"{city_territory(code)}"
    )


def test_a_shared_edge_belongs_to_exactly_one_city():
    """Half-open membership, so the Overpass post-filter cannot double-publish."""
    # Vancouver / New Westminster meet on the longitude -122.99.
    assert in_city_territory("CANWE", 49.2, -122.99)
    assert not in_city_territory("CAVAN", 49.2, -122.99)
    assert in_city_territory("CAVAN", 49.2, -122.9901)
    # And a missing coordinate belongs to nobody.
    assert not in_city_territory("CAVAN", None, -123.1)


def test_the_neighbours_are_carved_where_the_rollup_showed_duplicates():
    """Points that were published under both cities, each now in one."""
    cases = {
        (45.51, -73.51): ("CALON", "CAMTL"),   # Vieux-Longueuil riverfront
        (45.50, -73.56): ("CAMTL", "CALON"),   # downtown Montreal
        (45.65, -73.49): ("CAMTL", "CALON"),   # Pointe-aux-Trembles
        (43.60, -79.56): ("CAMIS", "CATOR"),   # Lakeview
        (43.60, -79.54): ("CATOR", "CAMIS"),   # Long Branch
        (43.75, -79.62): ("CATOR", "CAMIS"),   # Rexdale
        (49.20, -122.93): ("CANWE", "CAVAN"),
        (49.25, -123.10): ("CAVAN", "CANWE"),
        (43.85, -79.05): ("CAAJX", "CATOR"),
        (43.40, -79.80): ("CABUR", "CAMIS"),
        (38.896, -77.072): ("USNVA", "USWAS"),  # Rosslyn
        (38.905, -77.065): ("USWAS", "USNVA"),  # Georgetown
        (38.871, -77.056): ("USNVA", "USWAS"),  # the Pentagon
        (38.889, -77.050): ("USWAS", "USNVA"),  # Lincoln Memorial
        (38.851, -77.040): ("USNVA", "USWAS"),  # Reagan National
        (38.805, -77.045): ("USNVA", "USWAS"),  # Old Town Alexandria
    }
    for (lat, lon), (owner, other) in cases.items():
        assert in_city_territory(owner, lat, lon), (lat, lon, owner)
        assert not in_city_territory(other, lat, lon), (lat, lon, other)


# ---------------------------------------------------------------------------
# The city models read their staged OSM extract through the territory
# ---------------------------------------------------------------------------
#
# The extract fetches only the territory, but a staged file keeps the one it
# was cut with until the city's weekly re-extract: when USNVA carved Arlington
# out of USWAS (#171), USWAS's staged file still held 7,115 Virginia trees and
# the rollup carried 5,153 OSM ids under both cities.  Each model filters on
# read, so a territory change takes effect on the next city build.


def _osm_models() -> dict[str, Path]:
    from test_data_sources import city_models

    return {
        code: path for code, path in city_models().items()
        if f"{code.lower()}_osm_staging.parquet" in path.read_text(encoding="utf-8")
    }


def test_every_osm_partition_is_written_with_its_territory():
    from tools.sync_osm_territory import sync

    stale = [
        path.relative_to(RAW_DIR).as_posix() for path in _osm_models().values()
        if sync(path.read_text(encoding="utf-8")) != path.read_text(encoding="utf-8")
    ]
    assert not stale, (
        f"{stale}: the OSM datasource's `where` is not the city's CITY_TERRITORY; "
        "run `python tools/sync_osm_territory.py` from data/raw"
    )


@pytest.mark.parametrize("code", sorted(_osm_models()))
def test_the_territory_reaches_the_composed_osm_partition(code):
    """The `where` the planner sees, not the text: one disjunct per rectangle,
    on the raw coordinates the partition stores."""
    from test_data_sources import parsed

    ds = parsed(_osm_models()[code]).datasources[f"{code.lower()}_osm_tree_info"]
    assert ds.where is not None, f"{code}'s OSM partition reads its staged extract unfiltered"
    rendered = str(ds.where)
    for lat_min, lat_max, lon_min, lon_max in city_territory(code):
        assert (f"ref:local.raw_latitude >= {lat_min} and ref:local.raw_latitude < {lat_max} and "
                f"ref:local.raw_longitude >= {lon_min} and ref:local.raw_longitude < {lon_max}") in rendered
    assert rendered.count(" or ") == len(city_territory(code)) - 1


def test_osm_cities_are_every_city_with_a_staged_extract():
    """The parametrized check above covers every city whose model reads one."""
    assert {"USWAS", "USNVA", "USBOS", "CAMTL", "CALON"} <= set(_osm_models())
