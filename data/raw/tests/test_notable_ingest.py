"""The notable-register ingests: Milan's monumental trees and Tree Boston's
Big Old Trees, and the linking rule they share (shared/notable.py).

Offline: the sources are fixtures shaped as the live ones were on 2026-11-15.
What the merge then does with a linked or unlinked register row is
test_notable_reconciliation.py.
"""

from __future__ import annotations

import importlib.util
import math
import sys
from datetime import date
from pathlib import Path

import pyarrow as pa
import pytest

RAW_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAW_DIR))

from shared.ingest import DATA_SOURCES, NOTABLE_DATA_SOURCES  # noqa: E402
from shared.notable import (  # noqa: E402
    diameter_from_circumference_cm,
    genus_of,
    link_to_inventory,
)

M_PER_DEG = 111_320.0


def load(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, RAW_DIR / path)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str((RAW_DIR / path).parent))
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def milan():
    return load("milan_monumental_trees", "itmil/milan_monumental_trees.py")


@pytest.fixture(scope="module")
def milan_inventory():
    return load("milan_tree_info", "itmil/milan_tree_info.py")


@pytest.fixture(scope="module")
def treeboston():
    return load("treeboston_big_old_trees", "usbos/treeboston_big_old_trees.py")


def north(point, metres):
    return (point[0] + metres / M_PER_DEG, point[1])


def inventory(rows):
    """(tree_id, species, (lat, lon), dbh_in) -> the municipal ingest's shape."""
    return pa.table(
        {
            "tree_id": [r[0] for r in rows],
            "species": [r[1] for r in rows],
            "latitude": pa.array([r[2][0] for r in rows], type=pa.float64()),
            "longitude": pa.array([r[2][1] for r in rows], type=pa.float64()),
            "diameter_at_breast_height": pa.array([r[3] for r in rows], type=pa.float64()),
        }
    )


def register_row(species, point, dbh_in):
    return {"species": species, "latitude": point[0], "longitude": point[1], "dbh_in": dbh_in}


HERE = (45.4750, 9.2000)


# --- the labels ---------------------------------------------------------------


def test_every_notable_label_is_a_known_source():
    for code, label in NOTABLE_DATA_SOURCES.items():
        assert label == f"NOTABLE_{code}"
        assert label in DATA_SOURCES


# --- the linking rule ---------------------------------------------------------


def test_genus_reads_the_first_word_only():
    assert genus_of("Platanus acerifolia (Aiton) Willd") == "Platanus"
    assert genus_of("platanus x hispanica") == "Platanus"
    assert genus_of("Ulmus") == "Ulmus"
    assert genus_of(None) is None
    assert genus_of("Unknown") == "Unknown"  # a sentinel matches only itself


def test_a_multistem_girth_is_its_largest_stem():
    assert diameter_from_circumference_cm("625") == pytest.approx(625 / math.pi)
    assert diameter_from_circumference_cm("410 -300") == pytest.approx(410 / math.pi)
    assert diameter_from_circumference_cm("350 - 300") == pytest.approx(350 / math.pi)
    assert diameter_from_circumference_cm("") is None
    assert diameter_from_circumference_cm(None) is None


def test_a_register_tree_links_to_its_own_tree_not_the_nearest_one():
    """Milan's 07: a hackberry 1 m from an elm.  The elm is not it."""
    links = link_to_inventory(
        [register_row("Celtis australis", HERE, 53.0)],
        inventory([
            ("elm", "Ulmus laevis", north(HERE, 1), 26.0),
            ("hackberry", "Celtis australis", north(HERE, 18), 50.0),
        ]),
    )
    assert links == ["hackberry"]


def test_a_sapling_of_the_same_genus_is_not_the_monument():
    """Milan's 03: the only plane within 30 m is an 8 cm one."""
    links = link_to_inventory(
        [register_row("Platanus x hispanica", HERE, 78.0)],
        inventory([("sapling", "Platanus x hispanica", north(HERE, 21), 8 / 2.54)]),
    )
    assert links == [None]


def test_nothing_past_the_radius_links():
    links = link_to_inventory(
        [register_row("Quercus rubra", HERE, 58.0)],
        inventory([("far", "Quercus rubra", north(HERE, 31), 60.0)]),
    )
    assert links == [None]


def test_an_unmeasured_trunk_links_on_genus_and_distance():
    links = link_to_inventory(
        [register_row("Ginkgo biloba", HERE, None)],
        inventory([("g", "Ginkgo biloba", north(HERE, 5), 30.0)]),
    )
    assert links == ["g"]


def test_two_register_rows_never_share_a_tree():
    links = link_to_inventory(
        [register_row("Quercus bicolor", HERE, 42.0), register_row("Quercus bicolor", north(HERE, 3), 40.0)],
        inventory([("oak", "Quercus bicolor", north(HERE, 2), 41.0)]),
    )
    assert links == [None, "oak"]  # 1 m beats 2 m, and the loser stays unlinked


def test_an_empty_register_needs_no_inventory():
    assert link_to_inventory([], None) == []


# --- Milan --------------------------------------------------------------------


def feature(scheda, slug, species, girth, height="20", proposta="no", point=(9.2, 45.475)):
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": list(point)},
        "properties": {
            "N. SCHEDA": scheda,
            "Link": f"https://www.comune.milano.it/aree-tematiche/verde/manutenzione-progettazione/alberi-monumentali/{slug}",
            "SPECIE - NOME SCIENTIFICO": species,
            "CIRCONFERENZA FUSTO (cm)": girth,
            "ALTEZZA (m)": height,
            "CRITERI DI MONUMENTALITÀ": "età e/o dimensioni ",
            "PROPOSTA DICHIARAZIONE NOTEVOLE INTERESSE PUBBLICO": proposta,
        },
    }


def test_milan_names_a_tree_only_when_its_page_title_is_a_name(milan):
    assert milan.name_from_link(".../il-platano-siamese-di-villa-litta") == "Il Platano Siamese di Villa Litta"
    assert milan.name_from_link(".../la-quercia-di-piazza-xxiv-maggio") == "La Quercia di Piazza XXIV Maggio"
    assert (
        milan.name_from_link(".../il-platano-della-biodiversita-ai-giardini-montanelli")
        == "Il Platano della Biodiversità ai Giardini Montanelli"
    )
    assert milan.name_from_link(".../il-platano-del-1773-a-villa-litta") == "Il Platano del 1773 a Villa Litta"
    # a species and a place is a description, not a name
    assert milan.name_from_link(".../bagolaro-villa-belgiojoso") is None
    assert milan.name_from_link(".../pianta-affori") is None
    assert milan.name_from_link(None) is None


def test_milan_species_drops_the_botanical_authority(milan):
    assert milan.scientific_name("Platanus acerifolia (Aiton) Willd") == "Platanus acerifolia"
    assert milan.scientific_name("Celtis australis L.") == "Celtis australis"
    assert milan.scientific_name("Platanus x acerifolia (Aiton) Willd") == "Platanus x acerifolia"


def test_milan_significance_says_what_the_register_says(milan):
    text = milan.significance_for(feature("13/F205/MI/03", "x", "Taxodium disticum", "725", "25", "vigente")["properties"])
    assert text == (
        "Listed on Italy's national register of monumental trees (no. 13/F205/MI/03) "
        "for its age and/or size. Trunk girth 725 cm, height 25 m. "
        "Also declared of notable public interest."
    )
    assert "notable public interest" not in milan.significance_for(
        feature("03/F205/MI/03", "x", "Platanus", "625")["properties"]
    )


def test_milan_register_rows(milan):
    rows = milan.register_rows([
        feature("14/F205/MI/03", "il-platano-siamese-di-villa-litta", "Platanus acerifolia (Aiton) Willd", "410 -300"),
        feature("", "il-nameless", "Platanus", "300"),  # no register number: not a row
    ])
    assert len(rows) == 1
    (row,) = rows
    assert row["tree_id"] == "mil-mon-14-F205-MI-03"
    assert row["tree_name"] == "Il Platano Siamese di Villa Litta"
    assert row["dbh_in"] == pytest.approx(410 / math.pi / 2.54)
    assert row["significance_source"].endswith("/il-platano-siamese-di-villa-litta")


def test_milan_refuses_a_reshaped_register(milan):
    broken = feature("01/X", "x", "Platanus", "300")
    del broken["properties"]["CIRCONFERENZA FUSTO (cm)"]
    with pytest.raises(RuntimeError, match="missing"):
        milan.register_rows([broken])


def test_milan_build_publishes_names_and_links(milan):
    point = (9.2, 45.475)
    table = milan.build(
        [
            feature("14/F205/MI/03", "il-platano-siamese-di-villa-litta", "Platanus acerifolia", "410", point=point),
            feature("07/F205/MI/03", "bagolaro-giardini-montanelli", "Celtis australis L.", "424", point=(9.21, 45.48)),
        ],
        inventory([("mil-40114", "Platanus x hispanica", (45.475 + 9.7 / M_PER_DEG, 9.2), 127 / 2.54)]),
    )
    rows = {r["tree_id"]: r for r in table.to_pylist()}
    assert rows["mil-mon-14-F205-MI-03"]["linked_tree_id"] == "mil-40114"
    assert rows["mil-mon-14-F205-MI-03"]["tree_name"] == "Il Platano Siamese di Villa Litta"
    assert rows["mil-mon-14-F205-MI-03"]["data_source"] == "NOTABLE_ITMIL"
    assert rows["mil-mon-07-F205-MI-03"]["linked_tree_id"] is None
    assert rows["mil-mon-07-F205-MI-03"]["tree_name"] is None
    assert rows["mil-mon-07-F205-MI-03"]["significance"].startswith("Listed on Italy's")


# --- Milan's inventory --------------------------------------------------------


def test_milan_inventory_columns(milan_inventory):
    header = ";".join(milan_inventory.COLUMNS)
    body = "\n".join([
        header,
        "1;Platanus;x acerifolia;;205;12;1950-10-21T00:00+01:00[Europe/Paris];9.1714;45.5170",
        "2;Prunus;serrulata;Kanzan;8;2;1899-12-30T00:00+01:00[Europe/Paris];9.19;45.47",
        "3;Celtis;australis;;8;2;1895-10-21T00:00+01:00[Europe/Paris];9.19;45.471",
        "4;Taxodium;distichum;;210;10;1840-10-21T00:00+01:00[Europe/Paris];9.20;45.474",
        "5;;;;0;;2010-10-21T00:00+02:00[Europe/Paris];9.19;45.472",
    ]).encode()
    rows = {r["tree_id"]: r for r in milan_inventory.transform(milan_inventory.read_source(body)).to_pylist()}
    assert rows["mil-1"]["species"] == "Platanus x acerifolia"
    assert rows["mil-1"]["diameter_at_breast_height"] == pytest.approx(205 / 2.54)
    assert rows["mil-1"]["crown_width_m"] == 12
    assert rows["mil-1"]["plant_date"] == date(1950, 10, 21)
    assert rows["mil-2"]["cultivar"] == "Kanzan"
    assert rows["mil-2"]["plant_date"] is None  # the spreadsheet zero date
    assert rows["mil-3"]["plant_date"] is None  # an 1890s date on an 8 cm trunk
    assert rows["mil-4"]["plant_date"] == date(1840, 10, 21)  # a 210 cm swamp cypress may well be
    assert rows["mil-5"]["species"] is None


# --- Tree Boston --------------------------------------------------------------


HEADER = (
    "Tree Number", "Neighborhood", "Address", "Latitutde", "Longitude", "Common Name",
    "Scientific Name", "DBH", "Circumference", "Height", "Private or public", "Noticings",
)


def test_treeboston_keeps_only_placed_public_trees(treeboston):
    sheets = [
        ("Dorchester", [
            HEADER,
            ("Tree #1", "Dorchester", "31 Tonawanda street", 42.2990399, -71.0663265, "Ginkgo", "Ginkgo biloba ", "41.5 in.", "130 in.", "93.7 ft.", "Public", None),
            ("Tree #8", None),  # numbered, never filled in
            ("Tree #9", "Dorchester", "Cedar Grove Cemetary", 42.2813, -71.048, "Pine", "Pinus Strobus", "32 in.", None, None, "public", None),
            ("Tree #10", "Dorchester", "Cedar Grove Cemetary", 42.2813, -71.048, "Pear", "Pyrus Calleryana", "13 in.", None, None, "public", None),
            (None, None, None, "Circ. from Diameter formula: C = π(D)"),
        ]),
        ("Hyde Park", [
            HEADER,
            ("Tree # 7", "Hyde Park", "Ross Playground", None, None, "Linden", "Tilia cordata", 32.0, 100.0, None, "public"),
            ("Tree #9", "Hyde Park", "72 Hyde Park Ave", 42.29803, -71.11448, "Oak", "Quercus coccinea", 48.0, 150.0, "80 ft", "Private"),
        ]),
    ]
    rows = treeboston.registry_rows(sheets)
    assert [r["tree_id"] for r in rows] == ["treeboston-dorchester-1"]
    (row,) = rows
    assert row["dbh_in"] == 41.5
    assert row["tree_name"] is None
    assert "(Dorchester)" in row["significance"] and "41.5 in" in row["significance"]
    assert row["significance_source"] == "https://treeboston.org/bot/"


def test_treeboston_reads_a_renamed_header_by_its_leading_words(treeboston):
    header = list(HEADER)
    header[7] = "DBH (in.)"
    rows = treeboston.registry_rows([
        ("Allston", [tuple(header), ("Tree number #2", "Allston", "Ringer Park", 42.35107, -71.138226, "Zelkova", "Zelkova serrata", 29.0, 115.0, 69.0, "Public", None)]),
    ])
    assert rows[0]["tree_id"] == "treeboston-allston-2" and rows[0]["dbh_in"] == 29.0


def test_an_unreadable_sheet_publishes_no_rows_rather_than_failing_boston(treeboston, monkeypatch):
    def reshaped(_body):
        return [("Dorchester", [("Tree", "Where"), ("Tree #1", "here")])]

    class Response:
        content = b""

    monkeypatch.setattr(treeboston, "get_with_retry", lambda *a, **k: Response())
    monkeypatch.setattr(treeboston, "read_sheets", reshaped)
    assert treeboston.fetch_rows() == []

    table = treeboston.build([], None)
    assert table.num_rows == 0
    assert {"significance", "significance_source", "linked_tree_id"} <= set(table.schema.names)


# --- the model half -------------------------------------------------------------


@pytest.mark.parametrize("code", sorted(NOTABLE_DATA_SOURCES))
def test_notable_city_is_fully_wired(code):
    """A register the model does not bind publishes nothing, silently: the
    label must be in the enum, claimed by one partition, and that partition
    must re-bind the three columns the template leaves null."""
    import re

    from test_data_sources import city_models

    text = city_models()[code].read_text(encoding="utf-8")
    label = NOTABLE_DATA_SOURCES[code]
    assert re.search(rf"enum<string>\[[^\]]*'{label}'", text), f"{code}: {label} is not in the source enum"
    blocks = [b for b in re.findall(r"^root partial datasource.*?;", text, flags=re.S | re.M) if f"= '{label}'" in b]
    assert len(blocks) == 1, f"{code}: {len(blocks)} partitions claim {label}"
    (block,) = blocks
    for column in ("significance: ?raw_significance", "significance_source: ?raw_significance_source",
                   "linked_tree_id: ?raw_linked_tree_id"):
        assert column in block, f"{code}'s {label} partition does not bind {column}"
