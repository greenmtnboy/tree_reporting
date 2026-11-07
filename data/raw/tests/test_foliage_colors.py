"""The leaf and bark shades the map paints a species with.

Three columns of CSS hex on the enrichment table, filled by the model, the
form, the hero set or a leaf photo, in that order of trust. What must hold:
every stored value is `#rrggbb`, the hero set only fills what is empty, and a
photo with no leaf in it yields nothing rather than its background.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pyarrow as pa

RAW_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAW_DIR))

from shared.ingest import SPECIES_SENTINELS, sanitize_species  # noqa: E402
from enrichment._foliage_colors import (  # noqa: E402
    FOLIAGE_COLUMNS,
    HERO_FOLIAGE,
    dominant_colors,
    is_leaf_pixel,
    leaf_colors_from_pixels,
    with_hero_foliage,
)
from enrichment._tree_enrichment_helpers import normalize_hex_color, normalize_hex_colors  # noqa: E402

HEX = re.compile(r"^#[0-9a-f]{6}$")

SCHEMA = pa.schema([
    ("species", pa.string()),
    ("foliage_colors", pa.list_(pa.string())),
    ("fall_foliage_colors", pa.list_(pa.string())),
    ("trunk_color", pa.string()),
])


def _table(*rows: dict) -> pa.Table:
    return pa.Table.from_pylist(list(rows), schema=SCHEMA)


# ── normalisation ──────────────────────────────────────────────────────────────


def test_hex_is_stored_lowercase_with_a_hash():
    assert normalize_hex_color("#C62828") == "#c62828"
    assert normalize_hex_color(" c62828 ") == "#c62828"
    assert normalize_hex_color("#abc") == "#aabbcc"


def test_anything_that_is_not_a_colour_is_null():
    for bad in (None, "", "red", "#12345", "#1234567", "rgb(1,2,3)", 42):
        assert normalize_hex_color(bad) is None  # type: ignore[arg-type]


def test_lists_drop_junk_and_duplicates_and_keep_order():
    assert normalize_hex_colors(["#C62828", "orange", "#e65100", "#c62828"]) == ["#c62828", "#e65100"]
    assert normalize_hex_colors([]) is None
    assert normalize_hex_colors(["dark green"]) is None


# ── the hero set ───────────────────────────────────────────────────────────────


def test_hero_keys_are_accepted_species_names_as_the_ingest_writes_them():
    for species in HERO_FOLIAGE:
        assert sanitize_species(species) == species, species
        assert species not in SPECIES_SENTINELS


def test_hero_values_are_stored_hex_and_nothing_else():
    for species, shades in HERO_FOLIAGE.items():
        assert set(shades) <= set(FOLIAGE_COLUMNS), species
        assert shades.get("foliage_colors"), f"{species}: every hero has a growing-season leaf"
        for name in ("foliage_colors", "fall_foliage_colors"):
            for color in shades.get(name, []):
                assert HEX.match(color), (species, name, color)
        assert HEX.match(shades["trunk_color"]), species


def test_hero_fills_only_what_is_empty():
    table = with_hero_foliage(_table(
        {"species": "Acer rubrum", "foliage_colors": None, "fall_foliage_colors": ["#ff0000"], "trunk_color": None},
        {"species": "Ginkgo biloba", "foliage_colors": None, "fall_foliage_colors": None, "trunk_color": None},
        {"species": "Newtaxon alba", "foliage_colors": None, "fall_foliage_colors": None, "trunk_color": None},
    ))
    rows = {row["species"]: row for row in table.to_pylist()}
    maple = HERO_FOLIAGE["Acer rubrum"]
    assert rows["Acer rubrum"]["foliage_colors"] == maple["foliage_colors"]
    assert rows["Acer rubrum"]["fall_foliage_colors"] == ["#ff0000"], "a value the row had is kept"
    assert rows["Acer rubrum"]["trunk_color"] == maple["trunk_color"]
    assert rows["Ginkgo biloba"]["fall_foliage_colors"] == HERO_FOLIAGE["Ginkgo biloba"]["fall_foliage_colors"]
    assert rows["Newtaxon alba"] == {"species": "Newtaxon alba", "foliage_colors": None, "fall_foliage_colors": None, "trunk_color": None}
    assert table.schema == SCHEMA


def test_hero_leaves_a_table_with_no_hero_untouched():
    table = _table({"species": "Newtaxon alba", "foliage_colors": None, "fall_foliage_colors": None, "trunk_color": None})
    assert with_hero_foliage(table) is table


# ── from a leaf photo ──────────────────────────────────────────────────────────


def test_leaf_gate_keeps_greens_and_drops_sky_bark_and_shadow():
    assert is_leaf_pixel((62, 122, 58))
    assert is_leaf_pixel((160, 190, 60)), "yellow-green"
    assert not is_leaf_pixel((135, 206, 235)), "sky"
    assert not is_leaf_pixel((120, 110, 100)), "bark"
    assert not is_leaf_pixel((10, 20, 10)), "shadow"
    assert not is_leaf_pixel((240, 245, 240)), "glare"


def test_dominant_green_is_the_mean_of_the_leaf_not_the_background():
    leaf = [(60 + i % 5, 120 + i % 7, 56) for i in range(600)]
    sky = [(135, 206, 235)] * 1000
    bark = [(110, 100, 90)] * 400
    colors = leaf_colors_from_pixels(sky + leaf + bark)
    assert colors is not None and len(colors) == 1
    r, g, b = (int(colors[0][i:i + 2], 16) for i in (1, 3, 5))
    assert 60 <= r <= 65 and 120 <= g <= 127 and b == 56


def test_two_leaf_shades_are_both_reported_when_both_are_substantial():
    dark = [(40, 90, 40)] * 500
    light = [(140, 190, 70)] * 300
    assert dominant_colors(dark + light) == ["#285a28", "#8cbe46"]


def test_a_minor_second_shade_or_a_near_twin_collapses_to_one():
    main = [(60, 120, 56)] * 900
    assert dominant_colors(main + [(140, 190, 70)] * 50) == ["#3c7838"], "under the share floor"
    assert dominant_colors(main + [(70, 130, 60)] * 400) == ["#3c7838"], "too close to be a second shade"


def test_a_photo_with_no_leaf_in_it_yields_nothing():
    assert leaf_colors_from_pixels([(135, 206, 235)] * 2000) is None
    assert leaf_colors_from_pixels([(60, 120, 56)] * 10) is None, "too few pixels to be a leaf"
    assert leaf_colors_from_pixels([]) is None
