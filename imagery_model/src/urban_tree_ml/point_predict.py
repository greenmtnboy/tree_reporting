"""Fetch the NAIP imagery around a latitude/longitude, run a trained model on
it, and write a reviewer tile bundle -- for anywhere NAIP covers, not only the
chips of an evaluation cohort.

The tile is cut from the newest NAIP acquisition covering the point, read
straight from Planetary Computer's cloud-optimised GeoTIFFs (only the window
is transferred) and resampled bilinearly to the run's ``resolution_m``, the
way the 30 cm mosaics are built.  Items of that acquisition year that meet the
tile are composited, so a tile on an item seam still has pixels on both sides.

Tiles sit on a fixed grid in the imagery's projected CRS whose step is half a
tile, and the tile chosen is the one whose centre is nearest the point: the
point is always at least a quarter tile from the edge, and asking for two
nearby points returns the same tile id rather than two overlapping tiles.  The
chip id records the grid cell (``p{epsg}_e{west}_n{north}``, metres).

The bundle is the same ``TilePredictionBundleV1`` the cohort exporter writes
(``tile_bundle_export.build_bundle``), with ``cohort`` ``point``.  Its current
trees come from every published city parquet whose envelope meets the tile,
so a tile on a city boundary shows the trees of both.  The bundle's ``city``
is the one whose territory holds the tile centre.

The fetched window is cached under ``artifacts/point-tiles/coverage-v2`` with
its source items and an explicit coverage mask. Legacy point-tiles caches are
preserved but never used, including for context neighbours. Predicting the
same place with a later model reuses the versioned imagery.

    uv run --group imagery --group train python -m urban_tree_ml.point_predict \\
      --point 42.3505,-71.0760 --run sf-boston-naip-swin-30cm-v2-finetune \\
      --min-score 0.1 --threshold 0.13 --out ../reviewer/tiles/usbos-30cm-v2

Nothing here knows about train/validation/test splits: a point tile is for
curation, and a tree published from one inside a sealed test block leaks into
later evaluation of that block.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import io
import json
import math
import os
import re
from dataclasses import dataclass
from functools import lru_cache
import sys
from datetime import date
from pathlib import Path
from typing import Any

from urban_tree_ml.tile_bundle_export import (
    CrownCoefficients,
    InventoryReader,
    build_bundle,
    checkpoint_digest,
    imagery_version,
    read_json,
    tile_bounds_lonlat,
    tile_id,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
INGEST_PATH = REPO_ROOT / "data" / "raw" / "shared" / "ingest.py"
PARQUET_URL = "https://storage.googleapis.com/trilogy_public_models/duckdb/trees/{code}_tree_info_v2.parquet"
# One predictions file per city (data/raw/city_derived.preql).
PREDICTIONS_URL = "https://storage.googleapis.com/trilogy_public_models/duckdb/trees/{code}_tree_predictions_v2.parquet"
MIN_VALID_FRACTION = 0.9
IMAGERY_CACHE_VERSION = "coverage-v2"
# A NAIP search or read that stalls fails instead of hanging a bulk batch
# overnight (bulk_predict waits out a network failure and retries the tile).
# GDAL reads these from the environment when it opens a URL.
STAC_TIMEOUT_S = 60
for _option, _value in {"GDAL_HTTP_CONNECTTIMEOUT": "30", "GDAL_HTTP_TIMEOUT": "120",
                        "GDAL_HTTP_MAX_RETRY": "3", "GDAL_HTTP_RETRY_DELAY": "5"}.items():
    os.environ.setdefault(_option, _value)


def point_cache_root(artifacts: Path) -> Path:
    # Legacy masks marked out-of-source fill valid. Never silently reuse those
    # files, including as context neighbours. Keep them intact for rollback.
    return artifacts / "point-tiles" / IMAGERY_CACHE_VERSION


Box = tuple[float, float, float, float]  # lat_min, lat_max, lon_min, lon_max


# ---------------------------------------------------------------------------
# Pure pieces
# ---------------------------------------------------------------------------


def grid_tile_origin(x: float, y: float, tile_m: float) -> tuple[float, float]:
    """(west, north) of the half-tile-grid tile whose centre is nearest (x, y)."""
    half = tile_m / 2
    centre_x = round(x / half) * half
    centre_y = round(y / half) * half
    return centre_x - half, centre_y + half


def point_chip_id(epsg: int, west: float, north: float) -> str:
    return f"p{epsg}_e{int(round(west))}_n{int(round(north))}"


# A tile predicted alone sees zero padding at its border and the peak decoder
# takes any slope running off it for a peak: Boston's v13 bulk pass had five
# times the interior density in each tile's outer 0.6 m.  So tiles are
# predicted in context instead -- chip-sized windows (the input stays the size
# the model was trained on) that each keep only their core, everything further
# than a margin from the window's edge.  The cores tile the ground.
#
# The margin decides the cost.  At a quarter tile (128 px of 512) a window
# keeps a quarter of what it sees and each tile needs four of its own: the
# reviewer's "Predict at" and plans made before 2026-11-15.  Measured on v20
# (60 Boston tiles, a window's edge cells against the same ground seen from
# the middle of a window half a tile over, at 0.13): 4.4x the peaks in the
# outer 2 px, 1.25x at 8-15 px, and from 32 px in nothing separates an edge
# view from an interior one.  So a bulk plan uses 64 px: a core of 384 px,
# which tiles three tiles exactly (3 x 512 = 4 x 384), sixteen windows for
# nine tiles rather than thirty-six.  ``block_tiles`` is that arithmetic.
#
# The stitched maps carry a ring of the neighbours' cells as wide as the peak
# decoder's reach, so a crown peaking just across the block's edge is not also
# a peak on this side's border cell; peaks found in the ring are the
# neighbour's.
DEFAULT_MARGIN_PX = 64
WINDOW_BATCH = 8  # windows through the model at once; bounds memory, not speed


def block_tiles(pixels: int, margin: int) -> int:
    """How many tiles a side the smallest block is that windows of *pixels*
    with *margin* discarded on every side tile exactly: 1 at a quarter tile,
    3 at an eighth."""
    core = pixels - 2 * margin
    if margin <= 0 or core <= 0:
        raise ValueError(f"a margin of {margin} px leaves a {pixels} px window no core")
    return core // math.gcd(pixels, core)


def context_windows(image, pixels: int, margin: int | None = None):
    """The windows of a block's image (the block and *margin* pixels around it,
    ``context_image`` or ``block_image``), row by row: [n * n, bands, pixels,
    pixels].  *margin* defaults to a quarter tile, the single-tile block."""
    import numpy as np

    margin = pixels // 4 if margin is None else margin
    core = pixels - 2 * margin
    n = (image.shape[-1] - 2 * margin) // core
    return np.stack([
        image[:, r * core:r * core + pixels, c * core:c * core + pixels]
        for r in range(n) for c in range(n)
    ])


def stitch_cores(output: dict[str, Any], cells: int, ring: int = 0, origin: int = 0,
                 margin: int | None = None) -> dict[str, Any]:
    """One block's output maps from its windows' (``context_windows`` order):
    each window's core, *cells* output cells a side together, plus *ring*
    cells of the neighbours on every side (cell (ring, ring) is the block's
    first).

    *margin* is in output cells and defaults to half a core, the single-tile
    block.  *origin* is the window cell the maps start at, for maps computed
    on the kept region alone (``core_region``) rather than on the whole window."""
    count = next(iter(output.values())).shape[0]
    n = math.isqrt(count)
    core = cells // n if n else 0
    if n * n != count or cells % n or (margin is None and core % 2):
        raise ValueError(f"a block of {cells} output cells does not split into {count} window cores")
    margin = core // 2 if margin is None else margin
    if ring > margin:
        raise ValueError(f"a ring of {ring} cells reaches past the windows")

    def span(k: int) -> tuple[int, int]:
        # A window's core in its own cells, widened by the ring on the block's outer side.
        return (margin - (ring if k == 0 else 0) - origin,
                margin + core + (ring if k == n - 1 else 0) - origin)

    stitched = {}
    for key, value in output.items():
        block = value.new_empty((1, *value.shape[1:-2], cells + 2 * ring, cells + 2 * ring))
        for k in range(count):
            r, c = divmod(k, n)
            (r0, r1), (c0, c1) = span(r), span(c)
            row, col = r * core + (ring if r else 0), c * core + (ring if c else 0)
            block[0, ..., row:row + r1 - r0, col:col + c1 - c0] = value[k, ..., r0:r1, c0:c1]
        stitched[key] = block
    return stitched


def core_region(cells: int, ring: int = 0, margin: int | None = None) -> tuple[int, int]:
    """(first cell, size) of the part of a window's output ``stitch_cores`` can
    use: its core and the ring either side.  *cells* is the window's side and
    *margin* (default a quarter of it) the cells discarded at each edge."""
    margin = cells // 4 if margin is None else margin
    return margin - ring, cells - 2 * margin + 2 * ring


def windows_needed(n: int, core: int, pixels: int, tiles: list[tuple[int, int]], reach: int) -> list[int]:
    """Which of a block's n x n windows (``context_windows`` order) have a core
    within *reach* pixels of one of *tiles* ((column, row) in the block)."""
    def meets(k: int, position: int) -> bool:
        return position * pixels - reach < (k + 1) * core and k * core < (position + 1) * pixels + reach

    return [
        r * n + c for r in range(n) for c in range(n)
        if any(meets(c, column) and meets(r, row) for column, row in tiles)
    ]


def ingest_literals(names: tuple[str, ...], ingest_path: Path = INGEST_PATH) -> dict[str, Any]:
    """Top-level dictionaries of the ingest library, read as literals:
    importing it would pull in pytrilogy for a few dictionaries."""
    return module_literals(names, ingest_path)


def module_literals(names: tuple[str, ...], path: Path) -> dict[str, Any]:
    """Top-level literal assignments in *path*.  The city registries are
    generated into shared/city_registry.py and ingest.py re-exports them as
    `NAME = city_registry.NAME`; that reference is followed to the sibling
    file, so callers keep naming ingest.py."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: dict[str, Any] = {}
    for node in tree.body:
        target = getattr(node, "target", None) or (getattr(node, "targets", None) or [None])[0]
        name = getattr(target, "id", None)
        if name in names and node.value is not None:
            value = node.value
            if (isinstance(value, ast.Attribute) and isinstance(value.value, ast.Name)
                    and value.value.id == "city_registry"):
                found[name] = module_literals((value.attr,), path.with_name("city_registry.py"))[value.attr]
            else:
                found[name] = ast.literal_eval(value)
    return found


def city_boxes(ingest_path: Path = INGEST_PATH) -> tuple[dict[str, Box], dict[str, tuple[Box, ...]]]:
    """CITY_BOUNDS and CITY_TERRITORY."""
    found = ingest_literals(("CITY_BOUNDS", "CITY_TERRITORY"), ingest_path)
    return found["CITY_BOUNDS"], found.get("CITY_TERRITORY", {})


def staged_family(city: str, ingest_path: Path = INGEST_PATH) -> frozenset[str]:
    """*city* and every city whose predictions are staged in *city*'s file
    (PREDICTED_STAGED_WITH): Boston's run covers the towns carved out of its
    metro, and each town reads its own rows from Boston's staged file."""
    staged_with = ingest_literals(("PREDICTED_STAGED_WITH",), ingest_path).get("PREDICTED_STAGED_WITH", {})
    city = city.upper()
    return frozenset({city, *(code for code, host in staged_with.items() if host == city)})


def box_meets(box: Box, bounds: dict[str, float]) -> bool:
    lat_min, lat_max, lon_min, lon_max = box
    return (
        lat_min < bounds["north"] and bounds["south"] < lat_max
        and lon_min < bounds["east"] and bounds["west"] < lon_max
    )


def cities_meeting(bounds: dict[str, float], envelopes: dict[str, Box]) -> list[str]:
    return sorted(code for code, box in envelopes.items() if box_meets(box, bounds))


def owning_city(
    lat: float,
    lon: float,
    envelopes: dict[str, Box],
    territory: dict[str, tuple[Box, ...]],
) -> str | None:
    """The city whose territory holds the point; a city without carved
    territory owns its envelope, as the ingest treats it."""
    def holds(boxes: tuple[Box, ...]) -> bool:
        return any(a <= lat < b and c <= lon < d for a, b, c, d in boxes)

    carved = [code for code, boxes in territory.items() if holds(boxes)]
    if carved:
        return sorted(carved)[0]
    enveloped = sorted(
        code for code, box in envelopes.items() if code not in territory and holds((box,))
    )
    return enveloped[0] if enveloped else None


class MultiInventory:
    """Current trees from several city parquets, as one InventoryReader."""

    def __init__(self, readers: dict[str, InventoryReader]):
        self.readers = readers
        self.version = ";".join(f"{code}:{r.version}" for code, r in sorted(readers.items())) or "none"

    def within(self, bounds: dict[str, float]) -> list[dict[str, Any]]:
        seen: dict[str, dict[str, Any]] = {}
        for code in sorted(self.readers):
            for tree in self.readers[code].within(bounds):
                seen.setdefault(str(tree["treeId"]), tree)
        return [seen[k] for k in sorted(seen)]


# ---------------------------------------------------------------------------
# Imagery
# ---------------------------------------------------------------------------


def naip_catalog(stac_url: str):
    """The STAC catalog, signing assets for reading, with a request timeout."""
    import planetary_computer
    import pystac_client

    return pystac_client.Client.open(stac_url, modifier=planetary_computer.sign_inplace, timeout=STAC_TIMEOUT_S)


def search_items(lat: float, lon: float, year: int | None, stac_url: str) -> list[Any]:
    """NAIP items covering the point, newest acquisition year only."""
    catalog = naip_catalog(stac_url)
    search = catalog.search(
        collections=["naip"],
        intersects={"type": "Point", "coordinates": [lon, lat]},
        datetime=f"{year}-01-01/{year}-12-31" if year else None,
    )
    items = list(search.items())
    if not items:
        raise SystemExit(f"no NAIP item covers {lat},{lon}" + (f" in {year}" if year else ""))
    newest = max(item.datetime.year for item in items)
    return [item for item in items if item.datetime.year == newest]


def tile_items(items_at_point: list[Any], bounds: dict[str, float], stac_url: str) -> list[Any]:
    """Every item of the point's acquisition year that meets the tile."""
    year = items_at_point[0].datetime.year
    catalog = naip_catalog(stac_url)
    search = catalog.search(
        collections=["naip"],
        bbox=[bounds["west"], bounds["south"], bounds["east"], bounds["north"]],
        datetime=f"{year}-01-01/{year}-12-31",
    )
    by_id = {item.id: item for item in search.items()}
    for item in items_at_point:
        by_id.setdefault(item.id, item)
    # The point's own items first, so they win where items overlap.
    first = [item.id for item in items_at_point]
    return [by_id[i] for i in first] + [by_id[i] for i in sorted(by_id) if i not in first]


def read_tile(
    items: list[Any],
    *,
    crs: str,
    west: float,
    north: float,
    pixels: int,
    resolution_m: float,
    bands: list[int],
):
    """(image uint8 [bands, pixels, pixels], valid mask, ids of items used)."""
    import numpy as np
    import rasterio
    from rasterio.enums import ColorInterp, Resampling
    from rasterio.transform import from_origin
    from rasterio.vrt import WarpedVRT

    transform = from_origin(west, north, resolution_m, resolution_m)
    image = np.zeros((len(bands), pixels, pixels), dtype=np.uint8)
    valid = np.zeros((pixels, pixels), dtype=bool)
    used: list[str] = []
    for item in items:
        if valid.all():
            break
        with rasterio.open(item.assets["image"].href) as source:
            # No-nodata RGB+NIR sources return all_valid read_masks even outside
            # their footprint. A generated alpha carries actual warped coverage.
            # Read it explicitly: with five bands read_masks can still be all_valid.
            alpha = next((i for i, c in enumerate(source.colorinterp, 1)
                          if c == ColorInterp.alpha), None)
            with WarpedVRT(
                source, crs=crs, transform=transform, width=pixels, height=pixels,
                resampling=Resampling.bilinear, add_alpha=alpha is None,
            ) as warped:
                data = warped.read(bands)
                mask = (warped.read(alpha or warped.count) > 0)
                mask &= np.all(warped.read_masks(bands) > 0, axis=0)
        fill = mask & ~valid
        if fill.any():
            image[:, fill] = data[:, fill]
            valid |= fill
            used.append(item.id)
    return image, valid, used


def write_cached_tile(path: Path, image, *, crs: str, west: float, north: float,
                      resolution_m: float, valid=None) -> None:
    import numpy as np
    import rasterio
    from rasterio.enums import ColorInterp
    from rasterio.transform import from_origin
    from uuid import uuid4

    path.parent.mkdir(parents=True, exist_ok=True)
    valid = np.ones(image.shape[1:], dtype=bool) if valid is None else np.asarray(valid, dtype=bool)
    if valid.shape != image.shape[1:]:
        raise ValueError("coverage mask does not match image shape")
    temporary = path.parent / f"{path.stem}.{uuid4().hex}.tmp.tif"
    try:
        # Internal mask moves atomically with the image, unlike a .msk sidecar.
        with rasterio.Env(GDAL_TIFF_INTERNAL_MASK=True), rasterio.open(
            temporary, "w", driver="GTiff", width=image.shape[2], height=image.shape[1],
            count=image.shape[0], dtype="uint8", crs=crs,
            transform=from_origin(west, north, resolution_m, resolution_m), compress="deflate",
        ) as out:
            if image.shape[0] == 4:
                out.colorinterp = (ColorInterp.red, ColorInterp.green, ColorInterp.blue, ColorInterp.undefined)
            out.write(image)
            out.write_mask(valid.astype("uint8") * 255)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def tile_manifest(year: int | str, crs: str, west: float, north: float, resolution_m: float,
                  valid_fraction: float, candidates: list[Any], used: list[str]) -> dict[str, Any]:
    by_id = {item.id: item for item in candidates}
    return {
        "collection": "naip",
        "cache_version": IMAGERY_CACHE_VERSION,
        "year": str(year),
        "crs": crs,
        "west": west,
        "north": north,
        "resolution_m": resolution_m,
        "valid_fraction": valid_fraction,
        "sources": [
            {"item_id": i, "acquisition_datetime": by_id[i].datetime.isoformat()} for i in used
        ],
    }


def year_items(bounds: dict[str, float], year: int, stac_url: str) -> list[Any]:
    """Every NAIP item of *year* that meets *bounds*."""
    catalog = naip_catalog(stac_url)
    search = catalog.search(
        collections=["naip"],
        bbox=[bounds["west"], bounds["south"], bounds["east"], bounds["north"]],
        datetime=f"{year}-01-01/{year}-12-31",
    )
    return sorted(search.items(), key=lambda item: item.id)


# A bulk run walks its plan tile by tile, and each tile's context is its eight
# neighbours: nine reads a tile, six of them the tile before's.  Decoded tiles
# (1 MB each) are kept here so a tile is read once while the run is near it.
TILE_MEMORY = 96


@lru_cache(maxsize=TILE_MEMORY)
def _cached_pixels(tif_path: Path, modified_ns: int):
    """A cached tile's pixels; *modified_ns* keys out a file rewritten since."""
    import rasterio

    with rasterio.open(tif_path) as cached:
        image = cached.read()
        valid_fraction = float((cached.dataset_mask() > 0).mean())
    image.setflags(write=False)  # shared between callers
    return image, valid_fraction


def cached_tile(tif_path: Path, manifest_path: Path):
    """(image, valid fraction, manifest) of a tile in the window cache, or None
    when it is not there in the current cache version."""
    if not manifest_path.exists():
        return None
    manifest = read_json(manifest_path)
    if manifest.get("cache_version") != IMAGERY_CACHE_VERSION:
        return None
    return (*_cached_pixels(tif_path, tif_path.stat().st_mtime_ns), manifest)


def neighbour_tile(cache_dir: Path, *, crs: str, west: float, north: float, pixels: int,
                   resolution_m: float, bands: list[int], stac_url: str):
    """The cached grid tile at (west, north) of *cache_dir*'s year, read and cached
    when missing; zeros where that year has no imagery."""
    import numpy as np

    epsg = int(crs.removeprefix("EPSG:"))
    stem = cache_dir / f"{point_chip_id(epsg, west, north)}_{round(resolution_m * 100)}cm"
    tif_path, manifest_path = stem.with_name(stem.name + ".tif"), stem.with_name(stem.name + ".manifest.json")
    hit = cached_tile(tif_path, manifest_path)
    if hit is not None:
        return hit[0]
    year = int(cache_dir.name.removeprefix("naip"))
    affine = [resolution_m, 0.0, west, 0.0, -resolution_m, north]
    candidates = year_items(tile_bounds_lonlat(affine, crs, pixels, pixels), year, stac_url)
    if not candidates:
        return np.zeros((len(bands), pixels, pixels), dtype=np.uint8)
    image, valid, used = read_tile(
        candidates, crs=crs, west=west, north=north, pixels=pixels, resolution_m=resolution_m, bands=bands,
    )
    if used:
        write_cached_tile(tif_path, image, crs=crs, west=west, north=north,
                          resolution_m=resolution_m, valid=valid)
        manifest = tile_manifest(year, crs, west, north, resolution_m, float(valid.mean()), candidates, used)
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return image


_READ_POOL = None


def read_pool():
    """One pool of imagery-read threads for the process, never shut down.

    A pool per block, whose threads exit when it closes, deadlocked on
    Windows once ONNX Runtime's DirectML provider was loaded (--device dml):
    the second block's pool waited forever on a worker that had finished its
    last read and never finished exiting.  Threads that never exit avoid it.
    """
    global _READ_POOL
    if _READ_POOL is None:
        from concurrent.futures import ThreadPoolExecutor

        _READ_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="imagery-read")
    return _READ_POOL


def context_image(image, cache_dir: Path, *, crs: str, west: float, north: float, pixels: int,
                  resolution_m: float, bands: list[int], stac_url: str):
    """*image*, the grid tile at (west, north), with a quarter tile of each of its
    eight neighbours around it: [bands, 1.5 * pixels, 1.5 * pixels].

    A bulk plan has cached most neighbours already; the rest (past the plan's
    edge) are read once and cached like any other tile.
    """
    import numpy as np

    tile_m, margin = pixels * resolution_m, pixels // 4
    canvas = np.zeros((image.shape[0], 3 * pixels, 3 * pixels), dtype=image.dtype)
    around = [(dx, dy) for dy in (-1, 0, 1) for dx in (-1, 0, 1) if dx or dy]

    def read(offset: tuple[int, int]):
        dx, dy = offset
        return neighbour_tile(
            cache_dir, crs=crs, west=west + dx * tile_m, north=north - dy * tile_m,
            pixels=pixels, resolution_m=resolution_m, bands=bands, stac_url=stac_url,
        )

    # Together: a read is a decode (or, past the plan's edge, a download), and
    # GDAL releases the interpreter for both.
    tiles = dict(zip(around, read_pool().map(read, around)))
    tiles[(0, 0)] = image
    for (dx, dy), tile in tiles.items():
        canvas[:, (dy + 1) * pixels:(dy + 2) * pixels, (dx + 1) * pixels:(dx + 2) * pixels] = tile
    return canvas[:, pixels - margin:2 * pixels + margin, pixels - margin:2 * pixels + margin]


def block_image(images: dict[tuple[int, int], Any], cache_dir: Path, *, crs: str, west: float, north: float,
                tiles: int, pixels: int, margin: int, resolution_m: float, bands: list[int], stac_url: str):
    """A block of *tiles* x *tiles* grid tiles whose north-west corner is
    (west, north), with *margin* pixels of the tiles around it:
    [bands, tiles * pixels + 2 * margin, ...].

    *images* holds the tiles already in hand by (column, row) in the block,
    row 0 the northmost; every other tile, and the ring around the block, is
    read from the cache (``neighbour_tile``).
    """
    import numpy as np

    tile_m = pixels * resolution_m
    positions = [(c, r) for r in range(-1, tiles + 1) for c in range(-1, tiles + 1)]
    missing = [p for p in positions if p not in images]

    def read(position: tuple[int, int]):
        c, r = position
        return neighbour_tile(
            cache_dir, crs=crs, west=west + c * tile_m, north=north - r * tile_m,
            pixels=pixels, resolution_m=resolution_m, bands=bands, stac_url=stac_url,
        )

    found = {**images, **dict(zip(missing, read_pool().map(read, missing)))}
    first = next(iter(found.values()))
    canvas = np.zeros((first.shape[0], (tiles + 2) * pixels, (tiles + 2) * pixels), dtype=first.dtype)
    for (c, r), tile in found.items():
        canvas[:, (r + 1) * pixels:(r + 2) * pixels, (c + 1) * pixels:(c + 2) * pixels] = tile
    return canvas[:, pixels - margin:(tiles + 1) * pixels + margin, pixels - margin:(tiles + 1) * pixels + margin]


def render_png(image) -> bytes:
    import numpy as np
    from PIL import Image

    buffer = io.BytesIO()
    Image.fromarray(np.moveaxis(image[:3], 0, -1), mode="RGB").save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


def own_cohorts(run_dir: Path) -> list[Path]:
    """The run's evaluation-metadata files that evaluated *its own* checkpoint.

    A fine-tune also evaluates its parent (``parent-validation*``, whose
    metadata names the parent run's checkpoint); sorted by name that cohort
    comes first, and predicting with it would quietly be the parent model.
    """
    marker = f"/runs/{run_dir.name}/"
    return [
        path for path in sorted((run_dir / "evaluation").glob("*/evaluation-metadata.json"))
        if marker in str(read_json(path).get("checkpoint", "")).replace("\\", "/")
    ]


def city_cohort(cohorts: list[Path], city: str | None) -> Path | None:
    """The cohort whose config a prediction for *city* takes: its validation
    cohort (`validation-usbos`), else its newest flight's
    (`validation-uswas-naip-2025`), else any other cohort of the city; None
    when the run evaluated nothing there.  A suffix match alone missed every
    flight-named cohort and fell back to the run's first cohort, another
    city's."""
    if not city:
        return None
    lc = city.lower()
    pattern = re.compile(rf"^(?P<kind>[a-z]+(?:-[a-z]+)*?)-{lc}(?:-naip-(?P<year>\d{{4}}))?(?P<review>-review-[0-9a-f]+)?$")
    ranked = []
    for path in cohorts:
        match = pattern.match(path.parent.name)
        if match is None:
            continue
        ranked.append((match["kind"] != "validation", match["review"] is not None,
                       -int(match["year"] or 9999), path.parent.name, path))
    return min(ranked)[-1] if ranked else None


def resolve_device(name: str = "auto"):
    """(torch device, DirectML adapter or None) for a --device value: ``auto``
    (CUDA when present, else the CPU), any torch device string, or
    ``dml``/``dml:N``: adapter N through ONNX Runtime's DirectML provider (an
    AMD or Intel GPU on Windows), with torch on the CPU for everything else."""
    import torch

    if name == "dml" or name.startswith("dml:"):
        return torch.device("cpu"), int(name.partition(":")[2] or 0)
    if name == "auto":
        return torch.device("cuda" if torch.cuda.is_available() else "cpu"), None
    return torch.device(name), None


class OnnxNetwork:
    """The network's forward pass on ONNX Runtime, for a GPU torch cannot
    drive: an AMD or Intel card on Windows, through the DirectML provider.

    torch-directml was tried first and computes wrong values (eval-mode
    BatchNorm and the Swin shifted-window attention both diverge from the CPU
    by tens of percent); ONNX Runtime agrees with torch on the CPU to 1e-5 on
    every head, at ~40 ms a 512 px window on an RX 6800 XT against ~2 s on
    the CPU.  Each region (``forward_region``'s fixed slice; None for the
    whole output) and window size is exported once, with a free batch axis,
    and cached under *cache_dir*, keyed by the checkpoint's digest.
    """

    def __init__(self, network, cache_dir: Path, digest: str, *, adapter: int = 0,
                 providers: list[Any] | None = None):
        self.network = network
        self.cache_dir = Path(cache_dir)
        self.digest = digest[:12]
        self.providers = providers or [("DmlExecutionProvider", {"device_id": adapter}), "CPUExecutionProvider"]
        self.sessions: dict[tuple[Any, int], tuple[Any, list[str]]] = {}

    def _export(self, path: Path, region: tuple[int, int] | None, sample) -> None:
        import torch

        network = self.network

        class Outputs(torch.nn.Module):
            def __init__(self):
                super().__init__()
                self.network = network

            def forward(self, image):
                output = self.network(image) if region is None else self.network.forward_region(image, *region)
                return tuple(output[key] for key in sorted(output))

        with torch.inference_mode():
            probe = network(sample[:1]) if region is None else network.forward_region(sample[:1], *region)
        keys = sorted(probe)
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_suffix(".partial")
        torch.onnx.export(
            Outputs().eval(), (sample[:1].contiguous(),), str(partial), input_names=["image"], output_names=keys,
            dynamic_axes={"image": {0: "n"}, **{key: {0: "n"} for key in keys}}, opset_version=17, dynamo=False,
        )
        partial.replace(path)

    def _session(self, region: tuple[int, int] | None, sample) -> tuple[Any, list[str]]:
        side = int(sample.shape[-1])
        if (region, side) not in self.sessions:
            try:
                import onnxruntime
            except ImportError as error:
                raise SystemExit("--device dml needs onnxruntime-directml and onnx "
                                 "(uv sync --group directml)") from error
            name = "full" if region is None else f"r{region[0]}x{region[1]}"
            path = self.cache_dir / f"{self.digest}-{side}px-{name}.onnx"
            if not path.exists():
                self._export(path, region, sample)
            options = onnxruntime.SessionOptions()
            # The DirectML provider needs both (onnxruntime's DirectML notes).
            options.enable_mem_pattern = False
            options.execution_mode = onnxruntime.ExecutionMode.ORT_SEQUENTIAL
            session = onnxruntime.InferenceSession(str(path), options, providers=self.providers)
            self.sessions[(region, side)] = (session, [output.name for output in session.get_outputs()])
        return self.sessions[(region, side)]

    def __call__(self, tensor, region: tuple[int, int] | None = None) -> dict[str, Any]:
        import torch

        session, keys = self._session(region, tensor)
        outputs = session.run(keys, {"image": tensor.contiguous().numpy()})
        return {key: torch.from_numpy(value) for key, value in zip(keys, outputs)}


class Predictor:
    def __init__(self, artifacts: Path, run: str, city_hint: str | None, device_name: str = "auto"):
        import torch

        from urban_tree_ml.evaluation import load_network

        self.run_dir = artifacts / "runs" / run
        cohorts = own_cohorts(self.run_dir)
        if not cohorts:
            raise SystemExit(f"{self.run_dir} has no evaluation to take the config and taxonomy from")
        metadata_path = city_cohort(cohorts, city_hint) or cohorts[0]
        self.metadata = read_json(metadata_path)
        self.taxonomy = read_json(metadata_path.parent / "taxonomy.json")
        self.config = self.metadata["config"]
        normalization = read_json(artifacts / "chips" / self.config["dataset"] / "normalization.json")
        checkpoint = self.run_dir / "checkpoints" / Path(str(self.metadata["checkpoint"])).name
        if not checkpoint.exists():
            raise SystemExit(f"checkpoint not found locally: {checkpoint}")

        self.device, adapter = resolve_device(device_name)
        model = self.config["model"]
        self.network = load_network(
            checkpoint,
            backbone=model["backbone"],
            input_channels=int(model["input_channels"]),
            feature_channels=int(model["feature_channels"]),
            taxonomy=self.taxonomy,
        ).to(self.device).eval()
        self.mean = torch.tensor(normalization["mean"], dtype=torch.float32)[:, None, None]
        self.std = torch.tensor(normalization["std"], dtype=torch.float32)[:, None, None]
        imagery = self.config["imagery"]
        self.bands = [int(b) for b in imagery["bands"]]
        self.input_scale = float(imagery["input_scale"])
        self.chip_pixels = int(imagery["chip_pixels"])
        self.resolution_m = float(imagery["resolution_m"])
        self.stride = int(self.config["targets"]["output_stride"])
        self.checkpoint_sha256 = checkpoint_digest(self.metadata, self.run_dir)
        self.taxonomy_version = hashlib.sha256(
            json.dumps(self.taxonomy, sort_keys=True).encode("utf-8")
        ).hexdigest()[:12]
        self.onnx = None if adapter is None else OnnxNetwork(
            self.network, artifacts / "onnx" / run, self.checkpoint_sha256, adapter=adapter,
        )

    def _forward(self, images, region: tuple[int, int] | None = None) -> dict[str, Any]:
        """The network's output maps for a batch of chips, [n, bands, pixels, pixels];
        with *region* (first cell, size), only those output cells on each axis."""
        import torch

        tensor = torch.from_numpy(images.astype("float32") * self.input_scale)
        tensor = ((tensor - self.mean) / self.std).to(self.device)
        if self.onnx is not None:
            return self.onnx(tensor, region)
        with torch.inference_mode():
            if region is None:
                return self.network(tensor)
            return self.network.forward_region(tensor, *region)

    def _decode(self, output: dict[str, Any], chip_id: str, *, limit: int | None = None,
                min_score: float | None = None) -> list[dict[str, Any]]:
        from urban_tree_ml.evaluation import _decode_batch

        evaluation = self.config["evaluation"]
        return _decode_batch(
            output,
            [chip_id],
            max_detections_per_chip=limit or int(evaluation["max_detections_per_chip"]),
            nms_kernel=int(evaluation["nms_kernel"]),
            min_score=min_score,
        )

    def predict(self, image, chip_id: str) -> list[dict[str, Any]]:
        """Every decoded candidate, highest score first (as evaluation decodes them)."""
        return self._decode(self._forward(image[None]), chip_id)

    def predict_in_context(self, image, chip_id: str) -> list[dict[str, Any]]:
        """Every candidate of the tile at the centre of *image* (``context_image``),
        each output cell taken from the window it sits in the middle of."""
        cells, ring = self.chip_pixels // self.stride, int(self.config["evaluation"]["nms_kernel"]) // 2
        # Only the cells stitch_cores keeps: the same values as whole windows
        # (model.forward_region), without three quarters of the head layers.
        region = core_region(cells, ring)
        output = self._forward(context_windows(image, self.chip_pixels), region)
        records = []
        for record in self._decode(stitch_cores(output, cells, ring, origin=region[0]), chip_id):
            x, y = int(record["output_x"]) - ring, int(record["output_y"]) - ring
            if 0 <= x < cells and 0 <= y < cells:
                records.append({**record, "output_x": x, "output_y": y})
        return records

    def predict_block(self, image, margin: int, chip_ids: dict[tuple[int, int], str],
                      min_score: float) -> dict[tuple[int, int], list[dict[str, Any]]]:
        """The candidates scoring at least *min_score* of each tile in *chip_ids*
        ((column, row) in the block -> chip id), highest first, from a block's
        image (``block_image``).  Windows whose core no asked-for tile needs are
        not run."""
        import torch

        pixels, stride = self.chip_pixels, self.stride
        cells, ring = pixels // stride, int(self.config["evaluation"]["nms_kernel"]) // 2
        core = pixels - 2 * margin
        if margin % stride or core % stride:
            raise ValueError(f"a margin of {margin} px is not whole output cells of {stride} px")
        n = (image.shape[-1] - 2 * margin) // core
        block_cells = n * core // stride
        needed = windows_needed(n, core, pixels, list(chip_ids), ring * stride)
        windows = context_windows(image, pixels, margin)
        region = core_region(cells, ring, margin // stride)
        parts = [
            self._forward(windows[needed[k:k + WINDOW_BATCH]], region)
            for k in range(0, len(needed), WINDOW_BATCH)
        ]
        output = {}
        for key in parts[0]:
            done = torch.cat([part[key] for part in parts])
            # A window not run has no peaks: its centre is certain background.
            full = done.new_full((n * n, *done.shape[1:]), float("-inf") if key == "center_logits" else 0.0)
            full[needed] = done
            output[key] = full
        stitched = stitch_cores(output, block_cells, ring, origin=region[0], margin=margin // stride)
        side = block_cells + 2 * ring
        cap = int(self.config["evaluation"]["max_detections_per_chip"])
        records: dict[tuple[int, int], list[dict[str, Any]]] = {position: [] for position in chip_ids}
        for record in self._decode(stitched, "", limit=side * side, min_score=max(min_score, 1e-6)):
            x, y = int(record["output_x"]) - ring, int(record["output_y"]) - ring
            position = (x // cells, y // cells)
            if 0 <= x < block_cells and 0 <= y < block_cells and position in records and len(records[position]) < cap:
                records[position].append({
                    **record, "chip_id": chip_ids[position], "output_x": x % cells, "output_y": y % cells,
                })
        return records


@dataclass
class TileImagery:
    """A grid tile's imagery and where it sits."""

    crs: str
    chip_id: str
    west: float
    north: float
    affine: list[float]
    bounds: dict[str, float]
    image: Any
    valid_fraction: float
    manifest: dict[str, Any]
    cache_dir: Path  # the window cache of the tile's acquisition year


def load_tile(predictor: "Predictor", lat: float, lon: float, *, artifacts: Path, year: int | None,
              stac_url: str, epsg_hint: int | None = None) -> TileImagery:
    """The grid tile nearest a point: from the window cache, else read from NAIP
    and cached (``predict_tile`` says how *epsg_hint* and *year* choose)."""
    def locate(epsg: int) -> tuple[str, str, float, float]:
        from pyproj import Transformer

        crs = f"EPSG:{epsg}"
        x, y = Transformer.from_crs("EPSG:4326", crs, always_xy=True).transform(lon, lat)
        west, north = grid_tile_origin(x, y, tile_m)
        return crs, point_chip_id(epsg, west, north), west, north

    tile_m = predictor.chip_pixels * predictor.resolution_m
    suffix = f"_{round(predictor.resolution_m * 100)}cm"
    cache = point_cache_root(artifacts)
    items: list[Any] = []
    manifest_path = None
    if epsg_hint is not None:
        crs, chip_id, west, north = locate(epsg_hint)
        # naip2022 sorts before naip2023: the last match is the newest year.
        hits = sorted(cache.glob(f"naip{year or '*'}/{chip_id}{suffix}.manifest.json"))
        manifest_path = hits[-1] if hits else None
    if manifest_path is None:
        items = search_items(lat, lon, year, stac_url)
        props = items[0].properties
        crs, chip_id, west, north = locate(
            int(props.get("proj:epsg") or str(props["proj:code"]).removeprefix("EPSG:"))
        )
        year = items[0].datetime.year
        manifest_path = cache / f"naip{year}" / f"{chip_id}{suffix}.manifest.json"
    affine = [predictor.resolution_m, 0.0, west, 0.0, -predictor.resolution_m, north]
    bounds = tile_bounds_lonlat(affine, crs, predictor.chip_pixels, predictor.chip_pixels)

    tif_path = manifest_path.with_name(manifest_path.name.removesuffix(".manifest.json") + ".tif")
    hit = cached_tile(tif_path, manifest_path)
    if hit is not None:
        image, valid_fraction, manifest = hit
    else:
        if not items:
            # A hinted but obsolete manifest supplied the grid, not STAC items.
            year = int(manifest_path.parent.name.removeprefix("naip"))
            items = search_items(lat, lon, year, stac_url)
        candidates = tile_items(items, bounds, stac_url)
        image, valid, used = read_tile(
            candidates, crs=crs, west=west, north=north, pixels=predictor.chip_pixels,
            resolution_m=predictor.resolution_m, bands=predictor.bands,
        )
        valid_fraction = float(valid.mean())
        manifest = tile_manifest(year, crs, west, north, predictor.resolution_m, valid_fraction, candidates, used)
        if used:
            write_cached_tile(tif_path, image, crs=crs, west=west, north=north,
                              resolution_m=predictor.resolution_m, valid=valid)
            manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return TileImagery(crs, chip_id, west, north, affine, bounds, image, valid_fraction, manifest,
                       manifest_path.parent)


def tile_city(tile: TileImagery, lat: float, lon: float, city: str | None, envelopes: dict[str, Box],
              territory: dict[str, tuple[Box, ...]]) -> str | None:
    """The city a tile's bundle belongs to, or None (and why, on stderr) for a
    tile with too little imagery or in no city's territory."""
    if tile.valid_fraction < MIN_VALID_FRACTION:
        print(f"{lat},{lon}: only {tile.valid_fraction:.0%} of the tile has imagery; skipped", file=sys.stderr)
        return None
    centre_lat = (tile.bounds["north"] + tile.bounds["south"]) / 2
    centre_lon = (tile.bounds["east"] + tile.bounds["west"]) / 2
    city = (city or owning_city(centre_lat, centre_lon, envelopes, territory) or "").upper()
    if not city:
        print(f"{lat},{lon}: in no city's territory; pass --city", file=sys.stderr)
        return None
    return city


def tile_result(predictor: "Predictor", tile: TileImagery, detections: list[dict[str, Any]], city: str, *,
                run: str, envelopes: dict[str, Box], min_score: float, threshold: float,
                coefficients: "CrownCoefficients | None", readers: "dict[str, InventoryReader] | None",
                render: bool) -> dict[str, Any]:
    """A tile's bundle from its decoded candidates (``predict_tile``'s result)."""
    bounds, manifest, chip_id = tile.bounds, tile.manifest, tile.chip_id
    inventory = None
    if readers is not None:
        for code in cities_meeting(bounds, envelopes):
            if code not in readers:
                try:
                    readers[code] = InventoryReader(PARQUET_URL.format(code=code.lower()), PREDICTIONS_URL.format(code=code.lower()), code)
                except Exception as error:  # an unbuilt city has no parquet yet
                    print(f"{code}: no current trees ({error.__class__.__name__})", file=sys.stderr)
                    readers[code] = None  # type: ignore[assignment]
        inventory = MultiInventory({
            code: readers[code] for code in cities_meeting(bounds, envelopes) if readers.get(code)
        })
    records = [r for r in detections if float(r["score"]) >= min_score]
    dates = sorted(date.fromisoformat(s["acquisition_datetime"][:10]).isoformat() for s in manifest["sources"])
    version = imagery_version(manifest)
    tid = tile_id(city, version, chip_id)
    png = render_png(tile.image) if render else b""
    bundle = build_bundle(
        chip={"chip_id": chip_id, "column_offset": 0, "row_offset": 0},
        predictions=records,
        city=city,
        version=version,
        provider="naip",
        acquisition=(dates[0], dates[-1], [s["item_id"] for s in manifest["sources"]]),
        raster_affine=tuple(tile.affine),
        crs=tile.crs,
        resolution_m=predictor.resolution_m,
        chip_pixels=predictor.chip_pixels,
        stride=predictor.stride,
        run_id=run,
        checkpoint_sha256=predictor.checkpoint_sha256,
        taxonomy_version=predictor.taxonomy_version,
        taxonomy=predictor.taxonomy,
        confidence_threshold=threshold,
        coefficients=coefficients,
        inventory=inventory,
        png=png,
        image_url=f"{tid}.png",
        cohort="point",
    )
    return {"bundle": bundle, "records": records, "tileId": tid, "city": city, "png": png, "inventory": inventory}


def predict_tile(
    predictor: "Predictor",
    lat: float,
    lon: float,
    *,
    artifacts: Path,
    run: str,
    year: int | None,
    stac_url: str,
    city: str | None,
    envelopes: dict[str, Box],
    territory: dict[str, tuple[Box, ...]],
    min_score: float,
    threshold: float,
    coefficients: "CrownCoefficients | None",
    readers: "dict[str, InventoryReader] | None",
    epsg_hint: int | None = None,
    context: bool = False,
    render: bool = True,
) -> dict[str, Any] | None:
    """Fetch (or reuse the cached) grid tile nearest a point, predict it and build its bundle.

    Returns None for a tile with too little imagery or in no city's territory.
    *readers* caches inventory readers across calls; None skips the
    current-tree overlay.  bulk_predict.py runs the same path over a grid, so
    what it publishes is what the reviewer shows for the tile.

    *epsg_hint* (bulk_predict knows its grid's zone) looks for the tile in the
    window cache first -- *year*'s, or the newest cached -- and searches STAC
    only on a miss; without it every call searches, so a newer NAIP year is
    picked up.

    *context* predicts the tile with its neighbours' imagery around it
    (``context_image``, ``Predictor.predict_in_context``): no detections
    piled on the tile's edges, at four forward passes a tile.

    *render* False leaves the tile's PNG empty (and the bundle's image hash
    with it), for a caller that keeps only the detections.
    """
    tile = load_tile(predictor, lat, lon, artifacts=artifacts, year=year, stac_url=stac_url, epsg_hint=epsg_hint)
    city = tile_city(tile, lat, lon, city, envelopes, territory)
    if city is None:
        return None
    if context:
        surround = context_image(
            tile.image, tile.cache_dir, crs=tile.crs, west=tile.west, north=tile.north,
            pixels=predictor.chip_pixels, resolution_m=predictor.resolution_m, bands=predictor.bands,
            stac_url=stac_url,
        )
        detections = predictor.predict_in_context(surround, tile.chip_id)
    else:
        detections = predictor.predict(tile.image, tile.chip_id)
    return tile_result(
        predictor, tile, detections, city, run=run, envelopes=envelopes, min_score=min_score,
        threshold=threshold, coefficients=coefficients, readers=readers, render=render,
    )


def predict_block(
    predictor: "Predictor",
    wanted: dict[tuple[int, int], tuple[float, float]],
    *,
    tiles: int,
    margin: int,
    artifacts: Path,
    run: str,
    year: int | None,
    stac_url: str,
    city: str | None,
    envelopes: dict[str, Box],
    territory: dict[str, tuple[Box, ...]],
    min_score: float,
    threshold: float,
    coefficients: "CrownCoefficients | None",
    readers: "dict[str, InventoryReader] | None",
    epsg_hint: int | None = None,
    render: bool = True,
) -> dict[tuple[int, int], dict[str, Any] | None]:
    """``predict_tile`` in context for several tiles of one block at once.

    *wanted* maps a tile's (column, row) in a block of *tiles* x *tiles* grid
    tiles (row 0 the northmost) to a point in it.  The block is predicted from
    chip-sized windows that discard *margin* pixels at every edge and whose
    cores tile it (``block_tiles``), so its tiles share windows instead of
    each running four of their own.  Each tile's result is what
    ``predict_tile`` returns, None included.
    """
    loaded = {
        position: load_tile(predictor, lat, lon, artifacts=artifacts, year=year, stac_url=stac_url,
                            epsg_hint=epsg_hint)
        for position, (lat, lon) in wanted.items()
    }
    cities = {
        position: tile_city(loaded[position], *wanted[position], city, envelopes, territory)
        for position in wanted
    }
    results: dict[tuple[int, int], dict[str, Any] | None] = {position: None for position in wanted}
    live = [position for position in wanted if cities[position] is not None]
    if not live:
        return results
    (column, row), first = live[0], loaded[live[0]]
    tile_m = predictor.chip_pixels * predictor.resolution_m
    image = block_image(
        {position: tile.image for position, tile in loaded.items()}, first.cache_dir, crs=first.crs,
        west=first.west - column * tile_m, north=first.north + row * tile_m, tiles=tiles,
        pixels=predictor.chip_pixels, margin=margin, resolution_m=predictor.resolution_m,
        bands=predictor.bands, stac_url=stac_url,
    )
    detections = predictor.predict_block(
        image, margin, {position: loaded[position].chip_id for position in live}, min_score,
    )
    for position in live:
        results[position] = tile_result(
            predictor, loaded[position], detections[position], cities[position], run=run, envelopes=envelopes,
            min_score=min_score, threshold=threshold, coefficients=coefficients, readers=readers, render=render,
        )
    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_point(value: str) -> tuple[float, float]:
    try:
        lat_text, lon_text = value.split(",")
        lat, lon = float(lat_text), float(lon_text)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"expected LAT,LON, got {value!r}") from error
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise argparse.ArgumentTypeError(f"{value!r} is not a latitude,longitude")
    return lat, lon


def latest_complete_run(artifacts: Path) -> str:
    runs = [p for p in (artifacts / "runs").iterdir() if (p / "COMPLETE").exists()]
    if not runs:
        raise SystemExit("no complete run under artifacts/runs; pass --run")
    return max(runs, key=lambda p: (p / "COMPLETE").stat().st_mtime).name


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--point", type=parse_point, action="append", required=True, help="LAT,LON; repeat for several tiles")
    parser.add_argument("--artifacts", default="artifacts", help="artifacts root (default: ./artifacts)")
    parser.add_argument("--run", default=None, help="training run id (default: the newest complete run)")
    parser.add_argument("--year", type=int, default=None, help="NAIP acquisition year (default: the newest covering the point)")
    parser.add_argument("--city", default=None, help="bundle city code, when the point is in no city's territory")
    parser.add_argument("--min-score", type=float, default=0.1, help="export candidates scoring at least this (default 0.1)")
    parser.add_argument("--threshold", type=float, default=None, help="operating threshold the reviewer's slider starts at (default: the run's)")
    parser.add_argument("--no-inventory", action="store_true", help="skip the current-tree overlay")
    parser.add_argument("--context", action="store_true",
                        help="predict with the neighbouring tiles' imagery around the tile, as bulk_predict does (4x the passes)")
    parser.add_argument("--device", default="auto")
    parser.add_argument("--out", required=True, help="reviewer tile directory")
    args = parser.parse_args(argv)

    artifacts = Path(args.artifacts)
    run = args.run or latest_complete_run(artifacts)
    envelopes, territory = city_boxes()
    first_city = args.city or owning_city(*args.point[0], envelopes, territory)
    predictor = Predictor(artifacts, run, first_city, args.device)
    stac_url = str(predictor.config["imagery"].get("stac_url") or "https://planetarycomputer.microsoft.com/api/stac/v1")
    threshold = args.threshold if args.threshold is not None else float(predictor.config["evaluation"]["confidence_threshold"])
    coefficient_path = REPO_ROOT / "data" / "raw" / "crown_width_coefficients.csv"
    coefficients = CrownCoefficients.from_csv(coefficient_path) if coefficient_path.exists() else None
    readers: dict[str, InventoryReader] = {}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []

    for lat, lon in args.point:
        tile = predict_tile(
            predictor, lat, lon, artifacts=artifacts, run=run, year=args.year, stac_url=stac_url,
            city=args.city, envelopes=envelopes, territory=territory, min_score=args.min_score,
            threshold=threshold, coefficients=coefficients,
            readers=None if args.no_inventory else readers, context=args.context,
        )
        if tile is None:
            continue
        bundle, records, tid, city, inventory = tile["bundle"], tile["records"], tile["tileId"], tile["city"], tile["inventory"]
        (out / f"{tid}.png").write_bytes(tile["png"])
        (out / f"{tid}.json").write_text(json.dumps(bundle, indent=1), encoding="utf-8")
        above = sum(1 for r in records if float(r["score"]) >= threshold)
        results.append({
            "point": [lat, lon],
            "tileId": tid,
            "city": city,
            "predictions": len(records),
            "aboveThreshold": above,
            "inventoryTrees": len(bundle["inventoryTrees"]),
            "inventoryCities": sorted(inventory.readers) if inventory else [],
        })
        print(f"{tid}: {len(records)} candidates >= {args.min_score}, {above} >= {threshold}, "
              f"{len(bundle['inventoryTrees'])} current trees", file=sys.stderr)

    print(json.dumps({"run": run, "tiles": results}))
    return 0 if results else 1


if __name__ == "__main__":
    raise SystemExit(main())
