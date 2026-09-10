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

from _ingest_shared import (  # noqa: E402
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
    }
    for (lat, lon), (owner, other) in cases.items():
        assert in_city_territory(owner, lat, lon), (lat, lon, owner)
        assert not in_city_territory(other, lat, lon), (lat, lon, other)
