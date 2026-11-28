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

from shared.boundary import Boundary, boundary_codes, city_boundary  # noqa: E402
from shared.ingest import (  # noqa: E402
    CITY_BOUNDS,
    CITY_TERRITORY,
    boxes_intersect,
    carved_out_of,
    city_territory,
    city_territory_where,
    in_city_territory,
    territory_city,
    territory_family,
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
    """The whole point.  A pair here is a tree the rollup publishes twice.

    Two rectangle cities may not overlap at all.  A polygon town's rectangle
    is only its envelope: it may reach into the city it is carved out of and
    into a neighbouring polygon's envelope, and the test below checks the
    ground itself.
    """
    clashes = []
    for a, b in combinations(sorted(CITY_TERRITORY), 2):
        if city_boundary(a) is not None and (city_boundary(b) is not None or a in carved_out_of(b)):
            continue
        if city_boundary(b) is not None and b in carved_out_of(a):
            continue
        for x in CITY_TERRITORY[a]:
            for y in CITY_TERRITORY[b]:
                if boxes_intersect(x, y):
                    clashes.append((a, x, b, y))
    assert not clashes, f"territories intersect: {clashes}"


# ---------------------------------------------------------------------------
# Polygon towns (shared/boundary.py)
# ---------------------------------------------------------------------------

POLYGON_TOWNS = boundary_codes()


def _grid(box, steps: int = 120):
    lat_min, lat_max, lon_min, lon_max = box
    for i in range(steps):
        for j in range(steps):
            yield (lat_min + (i + 0.5) * (lat_max - lat_min) / steps,
                   lon_min + (j + 0.5) * (lon_max - lon_min) / steps)


def test_the_split_towns_have_boundaries():
    assert {"USBEL", "USBKL", "USCAM", "USSOM", "USWAT"} <= set(POLYGON_TOWNS)
    assert set(POLYGON_TOWNS) <= set(CITY_TERRITORY), "a boundary file for a city that is not wired"


@pytest.mark.parametrize("code", POLYGON_TOWNS)
def test_a_polygon_towns_rectangle_is_its_boundarys_envelope(code: str):
    """`tools/fetch_boundary.py` prints it: the box rounded outward to 4 dp, so
    the Overpass extract and every cheap pre-check cover the whole polygon and
    little else."""
    (box,) = city_territory(code)
    lat_min, lat_max, lon_min, lon_max = city_boundary(code).box
    assert box[0] <= lat_min < box[0] + 1e-4 and box[1] - 1e-4 < lat_max <= box[1], (code, box)
    assert box[2] <= lon_min < box[2] + 1e-4 and box[3] - 1e-4 < lon_max <= box[3], (code, box)


def test_a_point_in_a_carved_family_belongs_to_exactly_one_member():
    """Sampled over every polygon town's envelope: the polygons of one
    tessellation meet without overlapping, and the city they are carved out
    of gives up exactly their ground."""
    for code in POLYGON_TOWNS:
        family = territory_family(code)
        if family == (code,):
            continue  # a city alone on the map: its territory is its polygon by definition
        for lat, lon in _grid(city_territory(code)[0]):
            owners = [member for member in family if in_city_territory(member, lat, lon)]
            # Ground no member's polygon holds (Lexington, the harbour) is no
            # one's; ground inside this town's polygon is this town's alone.
            assert len(owners) <= 1, (code, lat, lon, owners)
            assert (owners == [code]) == city_boundary(code).contains(lat, lon), (code, lat, lon, owners)


def test_boston_is_its_own_boundary_among_its_neighbours():
    # A polygon carves nothing: Boston and the towns are one tessellation.
    assert city_boundary("USBOS") is not None and carved_out_of("USBOS") == ()
    assert {"USBEL", "USBKL", "USBOS", "USCAM", "USSOM", "USWAT"} <= set(territory_family("USSOM"))
    assert carved_out_of("USCAM") == () and territory_family("USSEA") == ("USSEA",)
    assert "USBOS" not in territory_family("USSFO")
    places = {
        (42.3736, -71.1190): "USCAM",  # Harvard Square
        (42.3967, -71.1223): "USSOM",  # Davis Square
        (42.3420, -71.1212): "USBKL",  # Coolidge Corner
        (42.3959, -71.1787): "USBEL",  # Belmont Center
        (42.3655, -71.1855): "USWAT",  # Watertown Square
        (42.3550, -71.0656): "USBOS",  # Boston Common
        (42.3370, -71.2090): "USNWT",  # Newton City Hall
        (42.4645, -71.0107): "USSAU",  # Saugus
        (42.4084, -71.0537): "USEVE",  # Everett
        (42.4473, -71.2245): None,  # Lexington: a town of its own, not a city here
        (42.3088, -71.1200): "USBOS",  # the Arnold Arboretum, Jamaica Plain
        (42.3620, -71.0200): "USBOS",  # Logan Airport, East Boston
        (42.3601, -71.0942): "USCAM",  # MIT, on the Cambridge bank of the Charles
        (42.3467, -71.0972): "USBOS",  # Fenway Park
    }
    for (lat, lon), owner in places.items():
        for member in territory_family("USBOS"):
            assert in_city_territory(member, lat, lon) == (member == owner), (lat, lon, member)


def test_a_city_alone_on_the_map_is_its_own_line():
    """A polygon needs no neighbour: a rectangle drawn around a city takes in
    its suburbs, and every OSM node and submission there was published as the
    city's (Portland's box held 15,034 OSM trees outside the city limits)."""
    assert territory_family("USSEA") == ("USSEA",) and carved_out_of("USSEA") == ()
    assert in_city_territory("USPOR", 45.5152, -122.6784)  # Pioneer Courthouse Square
    assert not in_city_territory("USPOR", 45.4871, -122.8037)  # Beaverton
    assert in_city_territory("USNYC", 40.7580, -73.9855)  # Times Square
    assert not in_city_territory("USNYC", 40.7178, -74.0431)  # Jersey City, across the Hudson
    assert not in_city_territory("USDUR", 35.9132, -79.0558)  # Chapel Hill


def test_a_place_cut_from_a_city_is_a_city_beside_it():
    """Issue #287: places #285 cut from a neighbour's old rectangle are
    polygon cities of their own, in the neighbour's family."""
    assert territory_family("USPOR") == ("USBVN", "USPOR") and carved_out_of("USPOR") == ()
    assert territory_city("USPOR", 45.4871, -122.8037) == "USBVN"  # Beaverton
    assert territory_city("USDUR", 35.9132, -79.0558) == "USCHH"  # Chapel Hill
    assert territory_city("USNPV", 41.7606, -88.3201) == "USAUR"  # Aurora
    assert territory_city("USLAN", 42.7370, -84.4839) == "USELN"  # East Lansing
    assert territory_city("USSAN", 32.6401, -117.0842) == "USCHV"  # Chula Vista
    assert in_city_territory("USLSV", 39.9778, -105.1319)
    assert not in_city_territory("USDEN", 39.9778, -105.1319)


def test_denver_is_its_own_line():
    """Denver's old metro box published Lakewood's and Aurora's OSM trees as
    its own; its polygon keeps the city, airport included, and nothing else."""
    assert carved_out_of("USDEN") == ()
    assert in_city_territory("USDEN", 39.7392, -104.9903)  # Civic Center
    assert in_city_territory("USDEN", 39.8561, -104.6737)  # the airport
    assert not in_city_territory("USDEN", 39.7047, -105.0814)  # Lakewood
    assert not in_city_territory("USDEN", 39.7294, -104.8319)  # Aurora, CO
    assert not in_city_territory("USDEN", 39.6330, -105.3300)  # Mountain Parks


def test_a_polygon_inside_a_rectangle_city_is_carved_out_of_it():
    """Santa Monica in Los Angeles' box, the District against Northern
    Virginia's staircase, Oakland's bay line against San Francisco's box."""
    assert carved_out_of("USLAX") == ("USSMO",)
    assert in_city_territory("USSMO", 34.0100, -118.4960)  # the pier
    assert not in_city_territory("USLAX", 34.0100, -118.4960)
    assert in_city_territory("USLAX", 34.0522, -118.2437)  # downtown Los Angeles
    assert "USWAS" in carved_out_of("USNVA") and "USOAK" in carved_out_of("USSFO")


def test_a_row_is_rehomed_within_its_family_and_nowhere_else():
    """The city a row names is a hint only among towns carved from each other."""
    assert territory_city("USBOS", 42.3736, -71.1190) == "USCAM"  # recorded as Boston, stands in Cambridge
    assert territory_city("USCAM", 42.3550, -71.0656) == "USBOS"  # nearest centre was Cambridge's
    assert territory_city("USCAM", 42.3967, -71.1223) == "USSOM"
    assert territory_city("USBOS", 42.3550, -71.0656) == "USBOS"
    assert territory_city("USBOS", 40.7128, -74.0060) is None  # New York names no member of Boston's family
    assert territory_city("USNYC", 42.3736, -71.1190) is None  # nor does a New York row move to Cambridge
    assert territory_city("USBOS", None, -71.1) is None
    assert territory_city("XXXXX", 42.36, -71.06) is None


def test_a_boundary_is_even_odd_and_reads_only_its_band():
    """A square with a square hole, and a second square beside it."""
    outer = [(0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 4.0), (0.0, 0.0)]
    hole = [(1.0, 1.0), (1.0, 3.0), (3.0, 3.0), (3.0, 1.0), (1.0, 1.0)]
    island = [(6.0, 0.0), (7.0, 0.0), (7.0, 1.0), (6.0, 1.0), (6.0, 0.0)]
    shape = Boundary([outer, hole, island])
    assert shape.contains(0.5, 0.5) and shape.contains(3.5, 2.0) and shape.contains(0.5, 6.5)
    assert not shape.contains(2.0, 2.0), "the hole is outside"
    assert not shape.contains(2.0, 5.0) and not shape.contains(9.0, 9.0)
    assert shape.box == (0.0, 4.0, 0.0, 7.0)
    assert shape.wkt().startswith("MULTIPOLYGON(((0.000000 0.000000,") and shape.wkt().count("((") == 2
    # Two squares that share the edge x = 4: a point on it is in exactly one.
    east = Boundary([[(4.0, 0.0), (8.0, 0.0), (8.0, 4.0), (4.0, 4.0), (4.0, 0.0)]])
    west = Boundary([outer])
    assert west.contains(2.0, 4.0) != east.contains(2.0, 4.0)


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
        # CABUR's first box reached into Hamilton; carved by both inventories.
        (43.256, -79.869): ("CAHAM", "CABUR"),  # downtown Hamilton
        (43.335, -79.905): ("CAHAM", "CABUR"),  # Waterdown
        (43.288, -79.790): ("CAHAM", "CABUR"),  # Hamilton Beach Strip
        (43.302, -79.860): ("CABUR", "CAHAM"),  # Aldershot
        (43.326, -79.800): ("CABUR", "CAHAM"),  # downtown Burlington
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
    # A polygon is applied by the extract (shared/osm.py), never in a model:
    # a model carrying one ships every town's corners in the cloud bundle.
    assert "geo_contains" not in city_territory_where(code)
    assert "geo_contains" not in rendered.lower(), rendered


def test_the_published_boundaries_are_the_files():
    """A worker reads the parquet BOUNDARY_PARQUET names, not the files (the
    bundle excludes them); the name is the files' hash, so a boundary added
    or edited without `tools/publish_boundaries.py --publish` fails here."""
    from shared.boundary import BOUNDARY_PARQUET, published_boundary_name

    assert BOUNDARY_PARQUET == published_boundary_name(), (
        "shared/boundary.py names a boundary parquet that is not boundaries/*.json; "
        "run `uv run tools/publish_boundaries.py --publish` from data/raw"
    )


def test_osm_cities_are_every_city_with_a_staged_extract():
    """The parametrized check above covers every city whose model reads one."""
    assert {"USWAS", "USNVA", "USBOS", "CAMTL", "CALON"} <= set(_osm_models())
