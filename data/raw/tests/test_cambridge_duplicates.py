"""Cambridge's portal feed is a union, and the ingest un-unions it.

`82zb-7qc9` carries a city arboricultural record, a Harvard campus survey under
`H`-prefixed ids, and a row per planting *site* that outlives the tree in it.
Each stage below removes one of those, and each failed silently before it
existed: 2,248 empty sites published as living trees, replanted wells rendering
two trees a centimetre apart, and the Harvard layer double-rendering the block
faces it shares with the city.

The cases pinned here are the ones that were measured against the live portal;
the numbers behind each threshold are in the ingest's own comments.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

RAW_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAW_DIR))

CAMBRIDGE_LAT = 42.3736


@pytest.fixture(scope="module")
def cam():
    spec = importlib.util.spec_from_file_location(
        "cambridge_tree_info", RAW_DIR / "usbos" / "cambridge_tree_info.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def row(tree_id, *, lat=CAMBRIDGE_LAT, lon=-71.1166, species=None, **extra):
    rec = {
        "treeid": tree_id,
        "the_geom": {"type": "Point", "coordinates": [lon, lat]},
        "scientific": species,
    }
    rec.update(extra)
    return rec


def offset(metres_north=0.0, metres_east=0.0):
    """A lat/lon that many metres from the reference point."""
    import math

    return (
        CAMBRIDGE_LAT + metres_north / 111_320.0,
        -71.1166 + metres_east / (111_320.0 * math.cos(math.radians(CAMBRIDGE_LAT))),
    )


# --- empty planting sites ---------------------------------------------------


def test_a_retired_site_is_not_a_tree(cam):
    records = [
        row("1", species="Acer rubrum", sitetype="Tree"),
        row("2", species="Acer rubrum", sitetype="Retired"),
        row("3", species="Tilia cordata", sitetype="Proposed Tree"),
        row("4", species="Quercus alba", sitetype="Stump"),
    ]
    kept, dropped, _ = cam.drop_sites_without_a_tree(records)
    assert [r["treeid"] for r in kept] == ["1"]
    assert dropped == {"Retired": 1, "Proposed Tree": 1, "Stump": 1}


def test_a_standing_tree_survives_whatever_else_is_wrong_with_it(cam):
    """`Spar` is a dead trunk and `Unknown` an unidentified tree -- both there.

    This is the call `shared.ingest` already makes for the `Dead` sentinel: an
    empty site has nothing to put on the map, a dead or unnamed tree does.
    """
    records = [
        row("1", species="Acer rubrum", sitetype="Spar"),
        row("2", species=None, sitetype="Unknown"),
        row("3", species="Tilia cordata", sitetype=""),
        row("4", species="Tilia cordata"),
    ]
    kept, dropped, unrecognised = cam.drop_sites_without_a_tree(records)
    assert len(kept) == 4
    assert dropped == {}
    assert unrecognised == set()


def test_an_unfamiliar_sitetype_is_reported_rather_than_guessed_at(cam):
    """The drop is keyed on exact strings, so a rename fails open and quietly."""
    records = [row("1", species="Acer rubrum", sitetype="Retired Site")]
    kept, dropped, unrecognised = cam.drop_sites_without_a_tree(records)
    assert [r["treeid"] for r in kept] == ["1"]
    assert unrecognised == {"Retired Site"}


# --- replanted tree wells ---------------------------------------------------


def test_the_replanting_survives_its_well(cam):
    """Well 10153, as the portal publishes it: an undated maple and the redbud
    that replaced it, one centimetre apart."""
    lat, lon = offset(metres_north=0.01)
    records = [
        row("10153", species="Acer platanoides", treewellid="10153", diameter="8.0"),
        row("1506842", species="Cercis canadensis", treewellid="10153",
            lat=lat, lon=lon, plantdate="2023-11-02T00:00:00.000"),
    ]
    kept, collapsed, unresolved = cam.collapse_replanted_wells(records)
    assert [r["treeid"] for r in kept] == ["1506842"]
    assert (collapsed, unresolved) == (1, 0)


@pytest.mark.parametrize(
    "mark", [{"siteretire": "paved over"}, {"cartegraph": "2024-10-01T00:00:00.000"}]
)
def test_a_retirement_mark_outranks_a_planting_date(cam, mark):
    """SITERETIREDREASON and CARTEGRAPHRETIREDATE say the record is retired
    outright, so they are read before any date is compared."""
    lat, lon = offset(metres_north=0.5)
    records = [
        row("1", species="Acer rubrum", treewellid="1",
            plantdate="2024-01-01T00:00:00.000", **mark),
        row("2", species="Tilia cordata", treewellid="1", lat=lat, lon=lon,
            plantdate="2009-01-01T00:00:00.000"),
    ]
    kept, collapsed, _ = cam.collapse_replanted_wells(records)
    assert [r["treeid"] for r in kept] == ["2"]
    assert collapsed == 1


def test_site_replanted_is_not_a_retirement_mark(cam):
    """SITEREPLANTED is set on 243 live trees, most planted since 2020, so a
    `Y` on the newer planting must not hand the well to the older one."""
    lat, lon = offset(metres_north=0.5)
    records = [
        row("100", species="Acer rubrum", treewellid="3",
            plantdate="2009-01-01T00:00:00.000"),
        row("150", species="Tilia cordata", treewellid="3", lat=lat, lon=lon,
            sitereplan="Y", plantdate="2022-11-30T00:00:00.000"),
    ]
    kept, collapsed, _ = cam.collapse_replanted_wells(records)
    assert [r["treeid"] for r in kept] == ["150"]
    assert collapsed == 1


def test_the_cartegraph_plant_date_stands_in_for_a_missing_one(cam):
    """Well 69188: the 2025 replacement Zelkova has only a Cartegraph plant
    date, and without it the 2024 tree won as the only dated row."""
    lat, lon = offset(metres_north=0.4)
    records = [
        row("1509617", species="Zelkova serrata", treewellid="69188",
            plantdate="2024-11-05T00:00:00.000", cartegra_1="2024-11-05T00:00:00.000"),
        row("1510440", species="Zelkova serrata", treewellid="69188", lat=lat, lon=lon,
            cartegra_1="2025-11-03T00:00:00.000"),
    ]
    kept, collapsed, _ = cam.collapse_replanted_wells(records)
    assert [r["treeid"] for r in kept] == ["1510440"]
    assert collapsed == 1


def test_an_unresolvable_well_keeps_every_row(cam):
    """No marker, no date, and ids that do not sort: both rows stay.

    A missed duplicate double-renders one dot; guessing which of two trees is
    gone removes a real one.
    """
    lat, lon = offset(metres_north=0.3)
    records = [
        row("A1", species="Acer rubrum", treewellid="7"),
        row("B2", species="Tilia cordata", treewellid="7", lat=lat, lon=lon),
    ]
    kept, collapsed, unresolved = cam.collapse_replanted_wells(records)
    assert len(kept) == 2
    assert (collapsed, unresolved) == (0, 1)


def test_a_reused_well_id_is_two_wells(cam):
    """Well ids repeat across the city -- the widest such group spans 4,967m."""
    lat, lon = offset(metres_east=400.0)
    records = [
        row("1", species="Acer rubrum", treewellid="9", siteretire="paved over"),
        row("2", species="Tilia cordata", treewellid="9", lat=lat, lon=lon,
            plantdate="2020-01-01T00:00:00.000"),
    ]
    kept, collapsed, _ = cam.collapse_replanted_wells(records)
    assert len(kept) == 2
    assert collapsed == 0


# --- field mapping ----------------------------------------------------------


def test_the_published_row_takes_the_cartegraph_plant_date_and_a_sentence_case_name(cam):
    table = cam.build_table([
        row("1", species="Zelkova serrata", commonname="Japanese Zelkova",
            cartegra_1="2025-09-01T00:00:00.000"),
        row("2", species="Platanus x acerifolia", commonname="London Planetree",
            plantdate="2019-10-01T00:00:00.000", cartegra_1="2019-10-01T00:00:00.000"),
    ]).to_pylist()
    assert [str(r["plant_date"]) for r in table] == ["2025-09-01", "2019-10-01"]
    assert [r["tree_name"] for r in table] == ["Japanese zelkova", "London planetree"]


# --- the Harvard re-survey --------------------------------------------------


def test_a_harvard_row_on_top_of_a_city_tree_is_absorbed(cam):
    lat, lon = offset(metres_north=1.0)
    records = [
        row("5512", species="Gleditsia triacanthos", diameter="12.5"),
        row("H1544190", species="Gleditsia triacanthos", lat=lat, lon=lon, diameter="0.0"),
    ]
    kept, absorbed, filled = cam.collapse_harvard_resurveys(records)
    assert [r["treeid"] for r in kept] == ["5512"]
    assert (absorbed, filled) == (1, 0)


def test_the_city_row_always_wins(cam):
    """The Harvard layer carries no diameter, date, address or inspector, so
    the row that survives has to be the city's whichever is closer to whom."""
    lat, lon = offset(metres_north=0.5)
    records = [
        row("H999", species="Gleditsia triacanthos", lat=lat, lon=lon),
        row("40404", species="Gleditsia triacanthos", diameter="14.0"),
    ]
    kept, absorbed, _ = cam.collapse_harvard_resurveys(records)
    assert [r["treeid"] for r in kept] == ["40404"]
    assert kept[0]["diameter"] == "14.0"


def test_harvard_fills_a_species_the_city_never_recorded(cam):
    lat, lon = offset(metres_north=1.0)
    records = [
        row("5512", species=None, diameter="12.5"),
        row("H1544190", species="Gleditsia triacanthos", lat=lat, lon=lon),
    ]
    kept, absorbed, filled = cam.collapse_harvard_resurveys(records)
    assert [r["treeid"] for r in kept] == ["5512"]
    assert kept[0]["scientific"] == "Gleditsia triacanthos"
    assert (absorbed, filled) == (1, 1)


def test_a_different_genus_is_a_different_tree(cam):
    """The DeWolfe St case that the first version of this got wrong.

    `H1544191` is an *Amelanchier* 2.3m from the honeylocust `5512`; matching
    on distance alone absorbed it and left the *Gleditsia* it sits beside
    standing -- hiding a real tree to keep a duplicate.
    """
    near_lat, near_lon = offset(metres_north=2.3)
    far_lat, far_lon = offset(metres_north=2.9)
    records = [
        row("5512", species="Gleditsia triacanthos", diameter="12.5"),
        row("H1544191", species="Amelanchier sp", lat=near_lat, lon=near_lon),
        row("H1544190", species="Gleditsia triacanthos", lat=far_lat, lon=far_lon),
    ]
    kept, absorbed, _ = cam.collapse_harvard_resurveys(records)
    # The Amelanchier stays; the honeylocust behind it is the one absorbed.
    assert sorted(r["treeid"] for r in kept) == ["5512", "H1544191"]
    assert absorbed == 1


def test_a_blank_species_is_not_a_contradiction(cam):
    lat, lon = offset(metres_north=1.0)
    records = [
        row("5512", species="Gleditsia triacanthos"),
        row("H1", species=None, lat=lat, lon=lon),
    ]
    _, absorbed, _ = cam.collapse_harvard_resurveys(records)
    assert absorbed == 1


def test_one_city_tree_absorbs_at_most_one_harvard_row(cam):
    """Harvard's survey is about twice as dense as the city's where they
    overlap, so without a 1:1 constraint one city tree would swallow a run."""
    a_lat, a_lon = offset(metres_north=0.5)
    b_lat, b_lon = offset(metres_north=1.5)
    records = [
        row("5512", species="Gleditsia triacanthos"),
        row("H1", species="Gleditsia triacanthos", lat=a_lat, lon=a_lon),
        row("H2", species="Gleditsia triacanthos", lat=b_lat, lon=b_lon),
    ]
    kept, absorbed, _ = cam.collapse_harvard_resurveys(records)
    assert absorbed == 1
    assert sorted(r["treeid"] for r in kept) == ["5512", "H2"]


def test_the_tightest_pair_is_matched_first(cam):
    """Greedy shortest-first, so a loose candidate cannot steal a tight one."""
    tight_lat, tight_lon = offset(metres_north=0.2)
    loose_lat, loose_lon = offset(metres_north=2.8)
    records = [
        row("CITY", species="Acer rubrum"),
        row("H_LOOSE", species="Acer rubrum", lat=loose_lat, lon=loose_lon),
        row("H_TIGHT", species="Acer rubrum", lat=tight_lat, lon=tight_lon),
    ]
    kept, absorbed, _ = cam.collapse_harvard_resurveys(records)
    assert absorbed == 1
    assert sorted(r["treeid"] for r in kept) == ["CITY", "H_LOOSE"]


def test_campus_interior_trees_are_left_alone(cam):
    """96% of the Harvard layer has no city tree within 5m and is the only
    record of those trees anywhere."""
    lat, lon = offset(metres_north=30.0)
    records = [
        row("5512", species="Gleditsia triacanthos"),
        row("H1", species="Gleditsia triacanthos", lat=lat, lon=lon),
    ]
    kept, absorbed, _ = cam.collapse_harvard_resurveys(records)
    assert len(kept) == 2
    assert absorbed == 0


def test_the_match_is_deterministic(cam):
    """Two equidistant candidates tie-break on the id, so a rebuild that
    reorders the portal's pages cannot change which tree_id survives."""
    a_lat, a_lon = offset(metres_north=1.0)
    b_lat, b_lon = offset(metres_north=-1.0)
    base = [
        row("5512", species="Gleditsia triacanthos"),
        row("H_B", species="Gleditsia triacanthos", lat=a_lat, lon=a_lon),
        row("H_A", species="Gleditsia triacanthos", lat=b_lat, lon=b_lon),
    ]
    first, _, _ = cam.collapse_harvard_resurveys(list(base))
    second, _, _ = cam.collapse_harvard_resurveys(list(reversed(base)))
    assert sorted(r["treeid"] for r in first) == sorted(r["treeid"] for r in second)


def test_the_stages_do_not_reach_past_their_thresholds(cam):
    """Both radii are calibrated numbers, not round ones -- a change to either
    should be a deliberate edit with a measurement behind it."""
    assert cam.MATCH_METRES == 3.0
    assert cam.WELL_SPAN_METRES == 5.0
