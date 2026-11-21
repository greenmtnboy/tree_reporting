"""The coordinate id: a tree's key when its source publishes no stable one.

The rule is in EXTENDING.md (step 1); COORDINATE_ID_CITIES lists the cities
keyed on it.  An id changes only if a tree is moved, so the format is pinned
against ids those cities have already published.
"""

import re
import sys
from pathlib import Path

import pyarrow as pa

RAW_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAW_DIR))

from shared.ingest import (
    coordinate_key,
    coordinate_tree_id,
    drop_stacked_coordinates,
)

COORDINATE_ID_CITIES = {
    "usphl/philadelphia_tree_info.py": '"phl"',
    "jptyo/tokyo_tree_info.py": '"tyo"',
    "calon/longueuil_tree_info.py": '"lgl"',
    "usgeg/spokane_tree_info.py": '"spokane"',
    "ushou/houston_tree_info.py": '"hou"',
    "cakel/kelowna_tree_info.py": '"kel"',
    "uspor/portland_tree_info.py": '"pdx-st"',
    "gbbfs/belfast_tree_info.py": '"belfast"',
    "usral/riverside_tree_info.py": "PARK_ID_PREFIX",
}


def test_the_id_is_the_position_to_seven_places():
    assert coordinate_tree_id("phl", -75.16523451234, 39.95231249999) == "phl--75.1652345_39.9523125"


def test_published_ids_keep_their_shape():
    """Rows from the published parquets, November 2026."""
    assert coordinate_tree_id("phl", -74.9618673, 40.0930215) == "phl--74.9618673_40.0930215"
    assert coordinate_tree_id("lgl", -73.3476641, 45.4666534) == "lgl--73.3476641_45.4666534"
    # Tokyo's ids were published with `-` before the helper existed; Tokyo
    # publishes the unrounded latitude, so the id rounds it.
    assert coordinate_tree_id("tyo", 139.0468301, 35.79405816, sep="-") == "tyo-139.0468301-35.7940582"


def test_the_key_ignores_a_float_writers_last_digit():
    """Longueuil's portal publishes one tree at two precisions."""
    assert coordinate_key(-73.50224994604028, 45.1) == coordinate_key(-73.5022499460403, 45.1)
    assert coordinate_tree_id("lgl", -73.50224994604028, 45.1) == coordinate_tree_id(
        "lgl", -73.5022499460403, 45.1
    )


def test_rounding_twice_changes_nothing():
    lon, lat = coordinate_key(-117.39233057186757, 47.670896677472186)
    assert coordinate_key(lon, lat) == (lon, lat)


def table(rows):
    ids, species, dbh = zip(*rows)
    return pa.table({"tree_id": list(ids), "species": list(species), "dbh": list(dbh)})


def test_different_trees_at_one_point_are_all_dropped():
    out = drop_stacked_coordinates(
        table([("a", "Acer", 1.0), ("p", "Acer", 1.0), ("p", "Quercus", 1.0), ("b", "Ulmus", 2.0)]),
        label="test",
        identical=("species", "dbh"),
    )
    assert out["tree_id"].to_pylist() == ["a", "b"]


def test_one_tree_recorded_twice_keeps_its_first_row():
    out = drop_stacked_coordinates(
        table([("p", "Acer", 1.0), ("a", "Ulmus", 2.0), ("p", "Acer", 1.0)]),
        label="test",
        identical=("species", "dbh"),
    )
    assert out["tree_id"].to_pylist() == ["p", "a"]


def test_without_identical_columns_every_stack_is_dropped():
    out = drop_stacked_coordinates(
        table([("p", "Acer", 1.0), ("p", "Acer", 1.0), ("a", "Ulmus", 2.0)]), label="test"
    )
    assert out["tree_id"].to_pylist() == ["a"]


def test_no_stack_returns_the_table_unchanged():
    t = table([("a", "Acer", 1.0), ("b", "Ulmus", 2.0)])
    assert drop_stacked_coordinates(t, label="test") is t


def test_coordinate_id_cities_use_the_shared_helpers():
    """A hand-rolled f-string or stack count would drift from the rule."""
    for rel, prefix in COORDINATE_ID_CITIES.items():
        source = (RAW_DIR / rel).read_text(encoding="utf-8")
        assert f"coordinate_tree_id({prefix}" in source, rel
        assert "drop_stacked_coordinates(" in source, rel
        # Docstrings may show the format; code may not build it by hand.
        assert not re.search(r'f"[^"\n]*\{lon:\.7f\}', source), rel
