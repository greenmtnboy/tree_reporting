"""Leaf and bark shades for the species table.

Three columns, all CSS hex `#rrggbb`: `foliage_colors` (the growing-season
leaf, most common shade first), `fall_foliage_colors` (the autumn leaf; empty
for an evergreen) and `trunk_color` (the mature bark). The map paints a tree
with the shade for the current season (src/src/lib/foliageSeason.ts), picking
among a list by tree id so a species with two autumn shades renders mixed.

Where a shade comes from, in order of trust:

1. the admin form (`enrichment/admin/server.py`), a reviewer who looked;
2. the LLM run, asked alongside everything else in `_tree_enrichment_models.py`;
3. `HERO_FOLIAGE` below, hand-authored for the most planted and most
   recognisable street trees, applied on every load wherever the row still
   has no value (`with_hero_foliage`) -- a floor, never an override;
4. the leaf photo, through `leaf_colors_from_pixels` -- the `photo_colors`
   provider of `enrichment/backfill.py` reads the species' iNaturalist leaf
   close-up and keeps the dominant greens. That only ever fills
   `foliage_colors`: a purple-leaved cultivar fails the green gate on purpose
   (a wrong green is worse than none), and the full-tree photo is too much sky
   and lawn to read bark from.

Nothing here touches the sentinels: `Unknown` has no leaves.
"""

from __future__ import annotations

import colorsys
from collections import defaultdict
from collections.abc import Iterable

import pyarrow as pa

FOLIAGE_COLUMNS = ("foliage_colors", "fall_foliage_colors", "trunk_color")

# Keyed by the accepted species name as the ingest writes it
# (`test_foliage_colors.py` checks every key survives `sanitize_species`).
# Values are mid-tones for a map marker, not botanical swatches: an autumn
# list is in the order the shades are usually seen on the tree.
HERO_FOLIAGE: dict[str, dict[str, object]] = {
    "Acer platanoides": {"foliage_colors": ["#3f7f3a"], "fall_foliage_colors": ["#f2c230"], "trunk_color": "#6f665d"},
    "Acer rubrum": {"foliage_colors": ["#3e7a3a"], "fall_foliage_colors": ["#c62828", "#e65100"], "trunk_color": "#7d7367"},
    "Acer saccharum": {"foliage_colors": ["#3e8a3c"], "fall_foliage_colors": ["#f9a825", "#ef6c00", "#d84315"], "trunk_color": "#6d655d"},
    "Betula papyrifera": {"foliage_colors": ["#4f9a3f"], "fall_foliage_colors": ["#f3c53d"], "trunk_color": "#f1ede4"},
    "Cercis canadensis": {"foliage_colors": ["#4c8a3a"], "fall_foliage_colors": ["#f1c232"], "trunk_color": "#6d5c4f"},
    "Cornus florida": {"foliage_colors": ["#3f7f3c"], "fall_foliage_colors": ["#9b2d30"], "trunk_color": "#6f6258"},
    "Eucalyptus globulus": {"foliage_colors": ["#6f8f7a"], "trunk_color": "#c8b8a2"},
    "Fagus sylvatica": {"foliage_colors": ["#3e7a37", "#5b2333"], "fall_foliage_colors": ["#b5651d"], "trunk_color": "#8f8a80"},
    "Ginkgo biloba": {"foliage_colors": ["#5d9b3c"], "fall_foliage_colors": ["#f4d03f"], "trunk_color": "#8d8276"},
    "Gleditsia triacanthos": {"foliage_colors": ["#5a9a3e"], "fall_foliage_colors": ["#f2c233"], "trunk_color": "#5e544c"},
    "Lagerstroemia indica": {"foliage_colors": ["#4a8a3a"], "fall_foliage_colors": ["#e0562a", "#f2a132"], "trunk_color": "#b99e85"},
    "Liquidambar styraciflua": {"foliage_colors": ["#3f8a3a"], "fall_foliage_colors": ["#b71c1c", "#6a1b9a", "#ef6c00", "#f9a825"], "trunk_color": "#7a6f66"},
    "Metasequoia glyptostroboides": {"foliage_colors": ["#5a9a4a"], "fall_foliage_colors": ["#b5602c"], "trunk_color": "#9a6b4a"},
    "Nyssa sylvatica": {"foliage_colors": ["#3b7a38"], "fall_foliage_colors": ["#b3121b", "#e0561f"], "trunk_color": "#6b625a"},
    "Picea pungens": {"foliage_colors": ["#7d9cae"], "trunk_color": "#7a6a5c"},
    "Pinus strobus": {"foliage_colors": ["#3b6b4a"], "trunk_color": "#6b5a4b"},
    "Platanus x hispanica": {"foliage_colors": ["#4e8a3e"], "fall_foliage_colors": ["#b58a3b"], "trunk_color": "#c9c2a6"},
    "Populus tremuloides": {"foliage_colors": ["#4f9a3a"], "fall_foliage_colors": ["#f6c343"], "trunk_color": "#d8d6c8"},
    "Prunus serrulata": {"foliage_colors": ["#4a8a3c"], "fall_foliage_colors": ["#e07b39", "#c9402b"], "trunk_color": "#6e5a4e"},
    "Quercus agrifolia": {"foliage_colors": ["#3d6b3a"], "trunk_color": "#5f5650"},
    "Quercus palustris": {"foliage_colors": ["#3e7f3a"], "fall_foliage_colors": ["#b4432a", "#8d5a2b"], "trunk_color": "#6e665e"},
    "Quercus rubra": {"foliage_colors": ["#3b7a3a"], "fall_foliage_colors": ["#a63a2a", "#8d5524"], "trunk_color": "#6b625a"},
    "Tilia cordata": {"foliage_colors": ["#3f8a3c"], "fall_foliage_colors": ["#e3c13c"], "trunk_color": "#7a7067"},
    "Ulmus americana": {"foliage_colors": ["#3f7f3a"], "fall_foliage_colors": ["#e3c13c"], "trunk_color": "#6f665d"},
    "Washingtonia robusta": {"foliage_colors": ["#4f8a3a"], "trunk_color": "#9c8b74"},
    "Zelkova serrata": {"foliage_colors": ["#3f7f3a"], "fall_foliage_colors": ["#c9772f", "#a93f2e"], "trunk_color": "#8a8278"},
}


def with_hero_foliage(table: pa.Table) -> pa.Table:
    """Fill the three shade columns from HERO_FOLIAGE where a row has none.

    A value the row already carries -- from the model, the form or a photo --
    is kept, so the hero set is a floor for the species the map shows most
    and never overrides a reviewer.
    """
    heroes = [HERO_FOLIAGE.get(species) for species in table.column("species").to_pylist()]
    if not any(heroes):
        return table
    for name in FOLIAGE_COLUMNS:
        index = table.schema.get_field_index(name)
        current = table.column(name).to_pylist()
        filled = [value if value is not None else (hero or {}).get(name) for value, hero in zip(current, heroes)]
        table = table.set_column(index, name, pa.array(filled, type=table.schema.field(name).type))
    return table


# ── From a leaf photo ──────────────────────────────────────────────────────────

RGB = tuple[int, int, int]


def is_leaf_pixel(rgb: RGB) -> bool:
    """A pixel that is plausibly leaf: yellow-green to blue-green, not washed out, not shadow."""
    hue, saturation, value = colorsys.rgb_to_hsv(rgb[0] / 255, rgb[1] / 255, rgb[2] / 255)
    return 45 / 360 <= hue <= 170 / 360 and saturation >= 0.2 and 0.12 <= value <= 0.95


def _distance(a: RGB, b: RGB) -> float:
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


def dominant_colors(
    pixels: Iterable[RGB],
    keep=is_leaf_pixel,
    max_colors: int = 2,
    min_share: float = 0.15,
    min_distance: float = 60.0,
    min_pixels: int = 50,
) -> list[str]:
    """The dominant shades among the pixels *keep* accepts, as hex, biggest first.

    Pixels are binned to 8 levels a channel; each returned colour is the mean
    of its bin, so a bin's edge does not pick the shade. A second colour must
    hold `min_share` of the kept pixels and sit `min_distance` from the first,
    or the leaf is one colour. Fewer than `min_pixels` kept means the photo is
    not of a leaf, and the answer is nothing rather than the background.
    """
    bins: dict[tuple[int, int, int], list[int]] = defaultdict(lambda: [0, 0, 0, 0])
    kept = 0
    for pixel in pixels:
        if not keep(pixel):
            continue
        kept += 1
        total = bins[(pixel[0] >> 5, pixel[1] >> 5, pixel[2] >> 5)]
        total[0] += pixel[0]
        total[1] += pixel[1]
        total[2] += pixel[2]
        total[3] += 1
    if kept < min_pixels:
        return []
    means = sorted(
        ((count, (r // count, g // count, b // count)) for r, g, b, count in bins.values()),
        reverse=True,
    )
    chosen: list[RGB] = []
    for count, mean in means:
        if len(chosen) >= max_colors:
            break
        if chosen and (count / kept < min_share or any(_distance(mean, c) < min_distance for c in chosen)):
            continue
        chosen.append(mean)
    return ["#%02x%02x%02x" % c for c in chosen]


def leaf_colors_from_pixels(pixels: Iterable[RGB]) -> list[str] | None:
    """`foliage_colors` as the table stores it, from an image's pixels; None when it is not a leaf photo."""
    return dominant_colors(pixels) or None


def leaf_colors_from_image_bytes(data: bytes, sample_px: int = 96) -> list[str] | None:
    """Decode an image and read its leaf shades; Pillow is loaded here, not at import."""
    from io import BytesIO

    from PIL import Image

    with Image.open(BytesIO(data)) as image:
        image = image.convert("RGB")
        image.thumbnail((sample_px, sample_px))
        return leaf_colors_from_pixels(image.getdata())


def fetch_leaf_colors(photo_url: str | None, timeout: float = 20.0) -> list[str] | None:
    """The leaf shades of the photo at *photo_url*, or None when there is no photo or no leaf in it."""
    if not photo_url:
        return None
    import requests

    response = requests.get(photo_url, timeout=timeout)
    response.raise_for_status()
    return leaf_colors_from_image_bytes(response.content)
