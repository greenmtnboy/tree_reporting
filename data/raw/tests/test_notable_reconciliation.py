"""What the shared cluster merge does with a notable register (`NOTABLE_`).

Boston's own build SQL, planned by pytrilogy and run against fixture rows, the
way test_predicted_reconciliation.py runs it.  A register row (Milan's
monumental trees, Tree Boston's Big Old Trees) is linked by its ingest to the
inventory tree it is, and the merge has to honour exactly that link:

* a linked row joins its tree's cluster and lends it its name and why it is
  notable, and nothing else -- the inventory keeps its species, trunk and
  position, and the register row is not published;
* it joins *that* tree even when it sits beside another one, because the grid
  never sees it;
* an unlinked row is a tree of its own, with its own values, even with an
  inventory tree or an OSM node a metre away;
* nothing anchors on a register row: an OSM node beside an unlinked one stays
  a tree of its own;
* a tree no register names publishes no name and no significance.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

RAW_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAW_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

P1 = (42.3000, -71.0800)
P2 = (42.3050, -71.0850)
P3 = (42.3100, -71.0900)
P4 = (42.3150, -71.0950)
NUDGE = 0.000012  # ~1.3 m of latitude


def near(point, times: int = 1):
    return (point[0] + NUDGE * times, point[1])


MUNICIPAL = [
    ("bos-oak", "CITY_OF_BOSTON", "Quercus bicolor", 40.0, None, P1),
    ("bos-elm", "CITY_OF_BOSTON", "Ulmus americana", 12.0, None, P2),
    ("bos-maple", "CITY_OF_BOSTON", "Acer rubrum", 10.0, None, P4),
]
OSM = [("osm-1", "OSM_USBOS", "Unknown", None, None, near(P3))]

# (tree_id, species, dbh, point, linked_tree_id, name, significance)
NOTABLE = [
    # The register put the oak a metre from the elm; its ingest linked it to
    # the oak.  The grid would have given it to the elm.
    ("tb-oak", "Quercus bicolor", 42.0, near(P2), "bos-oak", "The Swamp Oak", "A big old oak."),
    # No inventory tree: stands alone, with an OSM node beside it.
    ("tb-alone", "Ginkgo biloba", 41.5, P3, None, None, "A big old ginkgo."),
    # Beside a maple it is not (another genus, so the ingest did not link).
    ("tb-beside", "Salix alba", 52.0, near(P4), None, None, "A big old willow."),
]


def write_notable(conn, path: Path, rows) -> None:
    conn.execute("DROP TABLE IF EXISTS notable")
    conn.execute(
        """
        CREATE TABLE notable (
            tree_id VARCHAR, city VARCHAR, data_source VARCHAR, species VARCHAR,
            tree_name VARCHAR, plant_date DATE, diameter_at_breast_height DOUBLE,
            latitude DOUBLE, longitude DOUBLE, submission_photo_url VARCHAR, cultivar VARCHAR,
            crown_width_m DOUBLE, significance VARCHAR, significance_source VARCHAR,
            linked_tree_id VARCHAR
        )
        """
    )
    conn.executemany(
        "INSERT INTO notable VALUES (?, 'USBOS', 'NOTABLE_USBOS', ?, ?, NULL, ?, ?, ?, NULL, NULL, NULL, ?, 'https://treeboston.org/bot/', ?)",
        [(tid, sp, name, dbh, *pt, sig, link) for tid, sp, dbh, pt, link, name, sig in rows],
    )
    conn.execute(f"COPY notable TO '{path.as_posix()}' (FORMAT PARQUET)")


@pytest.fixture(scope="module")
def published(tmp_path_factory) -> dict[str, dict]:
    from test_predicted_reconciliation import run_boston

    return run_boston(
        tmp_path_factory.mktemp("notable_reconciliation"),
        municipal=MUNICIPAL,
        satellite=[],
        predicted=[],
        osm=OSM,
        writers={"treeboston_big_old_trees.py": lambda conn, path: write_notable(conn, path, NOTABLE)},
    )


def test_the_survivors(published):
    assert set(published) == {"bos-oak", "bos-elm", "bos-maple", "tb-alone", "tb-beside", "osm-1"}


def test_a_linked_row_names_its_own_tree_and_lends_nothing_else(published):
    oak = published["bos-oak"]
    assert oak["tree_name"] == "The Swamp Oak"
    assert oak["significance"] == "A big old oak."
    assert oak["significance_source"] == "https://treeboston.org/bot/"
    assert oak["tree_id"] == oak["cluster_id"]
    assert "tb-oak" in (oak["merged_tree_ids"] or "")
    assert "NOTABLE_USBOS" in oak["merged_sources"]
    # the inventory's own values, not the register's
    assert oak["diameter_at_breast_height"] == pytest.approx(40.0)
    assert oak["latitude"] == pytest.approx(P1[0])


def test_the_tree_beside_the_register_point_is_not_named(published):
    elm = published["bos-elm"]
    assert elm["tree_name"] is None and elm["significance"] is None
    assert elm["merged_sources"] == "CITY_OF_BOSTON"


def test_an_unlinked_row_is_a_tree_of_its_own(published):
    ginkgo = published["tb-alone"]
    assert ginkgo["species"] == "Ginkgo biloba"
    assert ginkgo["diameter_at_breast_height"] == pytest.approx(41.5)
    assert ginkgo["latitude"] == pytest.approx(P3[0])
    assert ginkgo["significance"] == "A big old ginkgo."
    assert ginkgo["merged_sources"] == "NOTABLE_USBOS"


def test_nothing_anchors_on_a_register_row(published):
    assert published["osm-1"]["merged_sources"] == "OSM_USBOS"


def test_the_grid_does_not_hand_an_unlinked_row_to_its_neighbour(published):
    assert published["tb-beside"]["species"] == "Salix alba"
    maple = published["bos-maple"]
    assert maple["significance"] is None
    assert maple["merged_sources"] == "CITY_OF_BOSTON"
