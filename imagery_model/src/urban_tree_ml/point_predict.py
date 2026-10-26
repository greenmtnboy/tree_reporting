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
import os
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
PREDICTIONS_URL = "https://storage.googleapis.com/trilogy_public_models/duckdb/trees/tree_predictions_v2.parquet"
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
# times the interior density in each tile's outer 0.6 m.  So a tile is
# predicted in context instead -- four chip-sized windows a quarter tile
# outside it, each keeping only its middle half, which together are exactly
# the tile and leave every kept cell at least a quarter tile from the edge of
# the window that saw it.  The input stays the size the model was trained on.
# The stitched maps carry a ring of the neighbours' cells as wide as the peak
# decoder's reach, so a crown peaking just across the seam is not also a peak
# on this side's border cell; peaks found in the ring are the neighbour's.
WINDOW_OFFSETS = ((0, 0), (1, 0), (0, 1), (1, 1))  # (column, row) in half tiles


def context_windows(image, pixels: int):
    """The four windows of a ``context_image`` (1.5 tiles a side), [4, bands, pixels, pixels]."""
    import numpy as np

    half = pixels // 2
    return np.stack([image[:, r * half:r * half + pixels, c * half:c * half + pixels] for c, r in WINDOW_OFFSETS])


def stitch_cores(output: dict[str, Any], cells: int, ring: int = 0) -> dict[str, Any]:
    """One tile's output maps from the four windows' (``context_windows`` order):
    the middle half of each window, *cells* output cells a side, plus *ring*
    cells of the neighbours on every side (cell (ring, ring) is the tile's first)."""
    if cells % 4:
        raise ValueError(f"a tile of {cells} output cells does not split into window cores")
    quarter, half = cells // 4, cells // 2
    if ring > quarter:
        raise ValueError(f"a ring of {ring} cells reaches past the windows")
    # A window's core in its own cells, widened by the ring on the tile's outer side.
    spans = ((quarter - ring, quarter + half), (quarter, quarter + half + ring))
    stitched = {}
    for key, value in output.items():
        tile = value.new_empty((1, *value.shape[1:-2], cells + 2 * ring, cells + 2 * ring))
        for k, (c, r) in enumerate(WINDOW_OFFSETS):
            (r0, r1), (c0, c1) = spans[r], spans[c]
            row, col = r * (half + ring), c * (half + ring)
            tile[0, ..., row:row + r1 - r0, col:col + c1 - c0] = value[k, ..., r0:r1, c0:c1]
        stitched[key] = tile
    return stitched


def city_boxes(ingest_path: Path = INGEST_PATH) -> tuple[dict[str, Box], dict[str, tuple[Box, ...]]]:
    """CITY_BOUNDS and CITY_TERRITORY, read as literals: importing the ingest
    library would pull in pytrilogy for two dictionaries."""
    tree = ast.parse(ingest_path.read_text(encoding="utf-8"))
    found: dict[str, Any] = {}
    for node in tree.body:
        target = getattr(node, "target", None) or (getattr(node, "targets", None) or [None])[0]
        name = getattr(target, "id", None)
        if name in ("CITY_BOUNDS", "CITY_TERRITORY") and node.value is not None:
            found[name] = ast.literal_eval(node.value)
    return found["CITY_BOUNDS"], found.get("CITY_TERRITORY", {})


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


def neighbour_tile(cache_dir: Path, *, crs: str, west: float, north: float, pixels: int,
                   resolution_m: float, bands: list[int], stac_url: str):
    """The cached grid tile at (west, north) of *cache_dir*'s year, read and cached
    when missing; zeros where that year has no imagery."""
    import numpy as np
    import rasterio

    epsg = int(crs.removeprefix("EPSG:"))
    stem = cache_dir / f"{point_chip_id(epsg, west, north)}_{round(resolution_m * 100)}cm"
    tif_path, manifest_path = stem.with_name(stem.name + ".tif"), stem.with_name(stem.name + ".manifest.json")
    if manifest_path.exists() and read_json(manifest_path).get("cache_version") == IMAGERY_CACHE_VERSION:
        with rasterio.open(tif_path) as cached:
            return cached.read()
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
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            tile = image if dx == dy == 0 else neighbour_tile(
                cache_dir, crs=crs, west=west + dx * tile_m, north=north - dy * tile_m,
                pixels=pixels, resolution_m=resolution_m, bands=bands, stac_url=stac_url,
            )
            canvas[:, (dy + 1) * pixels:(dy + 2) * pixels, (dx + 1) * pixels:(dx + 2) * pixels] = tile
    return canvas[:, pixels - margin:2 * pixels + margin, pixels - margin:2 * pixels + margin]


def render_png(image) -> bytes:
    import numpy as np
    from PIL import Image

    buffer = io.BytesIO()
    Image.fromarray(np.moveaxis(image[:3], 0, -1), mode="RGB").save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


class Predictor:
    def __init__(self, artifacts: Path, run: str, city_hint: str | None, device_name: str = "auto"):
        import torch

        from urban_tree_ml.evaluation import load_network

        self.run_dir = artifacts / "runs" / run
        cohorts = sorted((self.run_dir / "evaluation").glob("*/evaluation-metadata.json"))
        if not cohorts:
            raise SystemExit(f"{self.run_dir} has no evaluation to take the config and taxonomy from")
        preferred = [c for c in cohorts if city_hint and c.parent.name.endswith(city_hint.lower())]
        metadata_path = (preferred or cohorts)[0]
        self.metadata = read_json(metadata_path)
        self.taxonomy = read_json(metadata_path.parent / "taxonomy.json")
        self.config = self.metadata["config"]
        normalization = read_json(artifacts / "chips" / self.config["dataset"] / "normalization.json")
        checkpoint = self.run_dir / "checkpoints" / Path(str(self.metadata["checkpoint"])).name
        if not checkpoint.exists():
            raise SystemExit(f"checkpoint not found locally: {checkpoint}")

        self.device = torch.device(
            ("cuda" if torch.cuda.is_available() else "cpu") if device_name == "auto" else device_name
        )
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

    def _forward(self, images) -> dict[str, Any]:
        """The network's output maps for a batch of chips, [n, bands, pixels, pixels]."""
        import torch

        tensor = torch.from_numpy(images.astype("float32") * self.input_scale)
        tensor = ((tensor - self.mean) / self.std).to(self.device)
        with torch.inference_mode():
            return self.network(tensor)

    def _decode(self, output: dict[str, Any], chip_id: str) -> list[dict[str, Any]]:
        from urban_tree_ml.evaluation import _decode_batch

        evaluation = self.config["evaluation"]
        return _decode_batch(
            output,
            [chip_id],
            max_detections_per_chip=int(evaluation["max_detections_per_chip"]),
            nms_kernel=int(evaluation["nms_kernel"]),
        )

    def predict(self, image, chip_id: str) -> list[dict[str, Any]]:
        """Every decoded candidate, highest score first (as evaluation decodes them)."""
        return self._decode(self._forward(image[None]), chip_id)

    def predict_in_context(self, image, chip_id: str) -> list[dict[str, Any]]:
        """Every candidate of the tile at the centre of *image* (``context_image``),
        each output cell taken from the window it sits in the middle of."""
        cells, ring = self.chip_pixels // self.stride, int(self.config["evaluation"]["nms_kernel"]) // 2
        output = self._forward(context_windows(image, self.chip_pixels))
        records = []
        for record in self._decode(stitch_cores(output, cells, ring), chip_id):
            x, y = int(record["output_x"]) - ring, int(record["output_y"]) - ring
            if 0 <= x < cells and 0 <= y < cells:
                records.append({**record, "output_x": x, "output_y": y})
        return records


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
    """
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
    if manifest_path.exists() and read_json(manifest_path).get("cache_version") == IMAGERY_CACHE_VERSION:
        import rasterio

        manifest = read_json(manifest_path)
        with rasterio.open(tif_path) as cached:
            image = cached.read()
            valid_fraction = float((cached.dataset_mask() > 0).mean())
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
    if valid_fraction < MIN_VALID_FRACTION:
        print(f"{lat},{lon}: only {valid_fraction:.0%} of the tile has imagery; skipped", file=sys.stderr)
        return None

    centre_lat = (bounds["north"] + bounds["south"]) / 2
    centre_lon = (bounds["east"] + bounds["west"]) / 2
    city = (city or owning_city(centre_lat, centre_lon, envelopes, territory) or "").upper()
    if not city:
        print(f"{lat},{lon}: in no city's territory; pass --city", file=sys.stderr)
        return None

    inventory = None
    if readers is not None:
        for code in cities_meeting(bounds, envelopes):
            if code not in readers:
                try:
                    readers[code] = InventoryReader(PARQUET_URL.format(code=code.lower()), PREDICTIONS_URL, code)
                except Exception as error:  # an unbuilt city has no parquet yet
                    print(f"{code}: no current trees ({error.__class__.__name__})", file=sys.stderr)
                    readers[code] = None  # type: ignore[assignment]
        inventory = MultiInventory({
            code: readers[code] for code in cities_meeting(bounds, envelopes) if readers.get(code)
        })

    if context:
        surround = context_image(
            image, manifest_path.parent, crs=crs, west=west, north=north, pixels=predictor.chip_pixels,
            resolution_m=predictor.resolution_m, bands=predictor.bands, stac_url=stac_url,
        )
        detections = predictor.predict_in_context(surround, chip_id)
    else:
        detections = predictor.predict(image, chip_id)
    records = [r for r in detections if float(r["score"]) >= min_score]
    dates = sorted(date.fromisoformat(s["acquisition_datetime"][:10]).isoformat() for s in manifest["sources"])
    version = imagery_version(manifest)
    tid = tile_id(city, version, chip_id)
    png = render_png(image)
    bundle = build_bundle(
        chip={"chip_id": chip_id, "column_offset": 0, "row_offset": 0},
        predictions=records,
        city=city,
        version=version,
        provider="naip",
        acquisition=(dates[0], dates[-1], [s["item_id"] for s in manifest["sources"]]),
        raster_affine=tuple(affine),
        crs=crs,
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
