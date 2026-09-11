"""Export one reviewable bundle per imagery tile: image, predictions, inventory.

This is the handoff between the model and the reviewer's satellite page
(``reviewer/satellite.ts`` in the main repository).  A bundle is the
``TilePredictionBundleV1`` contract from ``PREDICTION_CURATION_HANDOFF.md``:
a PNG of the tile, the run's predictions on it with stable ids and
tile-local pixel coordinates, the tile's affine transform and CRS so a pixel
can be georeferenced without the raster, and the published inventory trees
the tile covers, each with the crown width the public prediction parquet
gives it.  The reviewer draws the last two over the first so a person can
see which detections the inventory already has.

Identity, because everything downstream keys on it:

* ``tileId`` is ``{city}-{imageryVersion}-{chip_id}``.  A chip id such as
  ``r000005_c000073`` is a row/column of one mosaic, not a slippy-map tile,
  so it is meaningless without the city and the imagery it was cut from.
* ``imageryVersion`` is derived from the sha256 of every source GeoTIFF in
  the mosaic manifest, so two mosaics built from the same eight NAIP items
  share a version and a re-download of a different item does not.
* ``predictionId`` is ``{chip_id}:{output_x}:{output_y}``, the output cell
  the detection came from -- stable across sorting and thresholding, and
  scoped by the layer's ``runId`` and ``checkpointSha256``.  It is not a
  tree id; a tree id is minted only when a reviewer publishes.

Coordinates follow ``evaluation._add_geography`` exactly: a prediction's
mosaic pixel is ``(column_offset + output_x * stride, row_offset + output_y
* stride)``, the raster's affine maps that integer pixel to the projected
CRS, and pyproj converts to EPSG:4326 with ``always_xy``.  No half-pixel
shift is added.  The tile affine exported here is the raster's affine
pre-multiplied by the chip's offset, so ``affine * (x_px, y_px)`` on a
tile-local pixel gives the same projected point.

Sealed test-split chips are never exported unless ``--allow-test`` is
passed; ground truth is never exported at all.  Crown widths for
predictions are DBH-derived allometric estimates from the shared Tallo
coefficient table, applied the way ``data/raw/tree_predictions.preql``
applies it, and are labelled as such.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = 1
CROWN_WIDTH_METHOD = "tallo_genus_power_law_from_dbh"
DEFAULT_CANDIDATE_RADIUS_M = 6.0
DEFAULT_INVENTORY_MARGIN_M = 15.0
DBH_CM_PER_INCH = 2.54
EARTH_RADIUS_M = 6_371_008.8


# ---------------------------------------------------------------------------
# Pure pieces
# ---------------------------------------------------------------------------


def prediction_id(chip_id: str, output_x: int, output_y: int) -> str:
    return f"{chip_id}:{int(output_x)}:{int(output_y)}"


def imagery_version(mosaic_manifest: dict[str, Any]) -> str:
    """A content identity for the mosaic: its year and a digest of its sources."""
    sources = mosaic_manifest.get("sources") or []
    digests = sorted(str(s.get("sha256") or s.get("item_id") or "") for s in sources)
    if not digests:
        raise ValueError("mosaic manifest lists no sources; cannot version the imagery")
    digest = hashlib.sha256("\n".join(digests).encode("utf-8")).hexdigest()[:12]
    provider = str(mosaic_manifest.get("collection") or "naip").lower()
    year = mosaic_manifest.get("year")
    return f"{provider}{year}-{digest}" if year else f"{provider}-{digest}"


def tile_id(city: str, version: str, chip_id: str) -> str:
    return f"{city.upper()}-{version}-{chip_id}"


def tile_affine(
    raster_affine: tuple[float, float, float, float, float, float],
    column_offset: int,
    row_offset: int,
) -> list[float]:
    """The tile-local affine, in rasterio order ``(a, b, c, d, e, f)``.

    ``x = a * col + b * row + c`` and ``y = d * col + e * row + f`` for a
    tile-local pixel ``(col, row)`` measured from the tile's top-left corner,
    the pixel's own top-left corner being the reference (no centre shift).
    """
    a, b, c, d, e, f = raster_affine
    return [
        a,
        b,
        c + a * column_offset + b * row_offset,
        d,
        e,
        f + d * column_offset + e * row_offset,
    ]


def apply_affine(affine: list[float], x_px: float, y_px: float) -> tuple[float, float]:
    a, b, c, d, e, f = affine
    return a * x_px + b * y_px + c, d * x_px + e * y_px + f


def pixel_to_lonlat(affine: list[float], crs: str, x_px: float, y_px: float) -> tuple[float, float]:
    """Tile-local pixel to (longitude, latitude), the way _add_geography does it."""
    from pyproj import Transformer

    x, y = apply_affine(affine, x_px, y_px)
    to_wgs84 = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    lon, lat = to_wgs84.transform(x, y)
    return float(lon), float(lat)


def haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dlmb = math.radians(lon2 - lon1)
    h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(h))


@dataclass(frozen=True)
class CrownFit:
    level: str
    taxon: str
    n: int
    b: float
    scale: float
    dbh_max_cm: float
    family: str | None = None


class CrownCoefficients:
    """The shared Tallo table, read the way tree_predictions.preql reads it."""

    def __init__(self, rows: Iterable[dict[str, str]]):
        self.by_genus: dict[str, CrownFit] = {}
        self.fallbacks: dict[str, CrownFit] = {}
        for r in rows:
            fit = CrownFit(
                level=r["fit_level"],
                taxon=r["fit_taxon"],
                n=int(r["n"]),
                b=float(r["b"]),
                scale=float(r["scale"]),
                dbh_max_cm=float(r["dbh_max_cm"]),
                family=(r.get("family") or None),
            )
            if r["level"] == "genus":
                self.by_genus[r["taxon"]] = fit
            else:
                self.fallbacks[f"{r['level']}:{r['taxon']}"] = fit

    @classmethod
    def from_csv(cls, path: Path) -> "CrownCoefficients":
        with Path(path).open(encoding="utf-8", newline="") as handle:
            return cls(csv.DictReader(handle))

    def crown_width_m(self, genus: str | None, dbh_in: float | None) -> tuple[float, CrownFit] | None:
        """``2 * scale * dbh_cm ** b`` with dbh clamped to [1, dbh_max_cm].

        A palm's crown does not scale with its stem and Tallo has no palm
        crowns, so an Arecaceae genus gets no estimate; an unknown genus
        takes the angiosperm fit, which is what the public prediction parquet
        does for a tree whose growth form the enrichment table cannot supply.
        """
        if dbh_in is None or not math.isfinite(dbh_in) or dbh_in <= 0:
            return None
        fit = self.by_genus.get(genus or "")
        if fit is not None and (fit.family or "") == "Arecaceae":
            return None
        if fit is None:
            fit = self.fallbacks.get("division:Angiosperm") or self.fallbacks.get("global:all")
        if fit is None:
            return None
        dbh_cm = min(max(dbh_in * DBH_CM_PER_INCH, 1.0), fit.dbh_max_cm)
        return 2 * fit.scale * dbh_cm**fit.b, fit


def candidate_tree_ids(
    lon: float,
    lat: float,
    inventory: Iterable[dict[str, Any]],
    radius_m: float = DEFAULT_CANDIDATE_RADIUS_M,
) -> list[str]:
    """Inventory trees within *radius_m*, nearest first.  Suggestions only;
    an identity is merged only when a reviewer says so."""
    scored = []
    for tree in inventory:
        if tree.get("longitude") is None or tree.get("latitude") is None:
            continue
        d = haversine_m(lon, lat, tree["longitude"], tree["latitude"])
        if d <= radius_m:
            scored.append((d, tree["treeId"]))
    return [tree_id for _, tree_id in sorted(scored)]


def pixel_lonlat_affine(affine: list[float], crs: str, width: int, height: int) -> list[float]:
    """A pixel -> (longitude, latitude) affine fitted at the tile's corners.

    The reviewer has no projection library, and a nudged crown centre needs
    a coordinate.  Over one 154 m tile the projected-to-geographic mapping is
    linear to well under a centimetre, so an affine through three corners is
    exact for the purpose: ``lon = a*x + b*y + c``, ``lat = d*x + e*y + f``.
    ``test_tile_bundle_export`` checks it against pyproj at the far corner.
    """
    lon0, lat0 = pixel_to_lonlat(affine, crs, 0, 0)
    lon_x, lat_x = pixel_to_lonlat(affine, crs, width, 0)
    lon_y, lat_y = pixel_to_lonlat(affine, crs, 0, height)
    return [
        (lon_x - lon0) / width, (lon_y - lon0) / height, lon0,
        (lat_x - lat0) / width, (lat_y - lat0) / height, lat0,
    ]


def tile_bounds_lonlat(affine: list[float], crs: str, width: int, height: int) -> dict[str, float]:
    corners = [
        pixel_to_lonlat(affine, crs, x, y)
        for x, y in ((0, 0), (width, 0), (0, height), (width, height))
    ]
    return {
        "west": min(c[0] for c in corners),
        "east": max(c[0] for c in corners),
        "south": min(c[1] for c in corners),
        "north": max(c[1] for c in corners),
    }


def build_prediction(
    row: dict[str, Any],
    *,
    taxonomy: dict[str, Any],
    stride: int,
    affine: list[float],
    crs: str,
    coefficients: CrownCoefficients | None,
    inventory: list[dict[str, Any]],
    candidate_radius_m: float = DEFAULT_CANDIDATE_RADIUS_M,
) -> dict[str, Any]:
    """One prediction in the bundle's shape, from one predictions.parquet row.

    Taxon ids are resolved with *this run's* taxonomy; the exported ``species``
    and ``genus`` columns are trusted when present and recomputed otherwise.
    Longitude/latitude are taken from the export when present (they were
    computed by ``_add_geography`` on the source raster) and computed from the
    tile affine otherwise; the two agree to floating-point precision, which
    ``test_tile_bundle_export`` checks.
    """
    x_px = int(row["output_x"]) * stride
    y_px = int(row["output_y"]) * stride
    if row.get("longitude") is not None and row.get("latitude") is not None:
        lon, lat = float(row["longitude"]), float(row["latitude"])
    else:
        lon, lat = pixel_to_lonlat(affine, crs, x_px, y_px)

    genera = taxonomy.get("genera") or []
    species_names = taxonomy.get("species") or []

    def taxon(value: Any, names: list[str]) -> str | None:
        if value is None:
            return None
        try:
            index = int(value)
        except (TypeError, ValueError):
            return None
        return names[index] if 0 <= index < len(names) else None

    genus = row.get("genus") or taxon(row.get("genus_id"), genera)
    species = row.get("species") or taxon(row.get("species_id"), species_names)
    dbh_in = row.get("dbh_in")
    dbh_in = float(dbh_in) if dbh_in is not None and math.isfinite(float(dbh_in)) else None

    crown = coefficients.crown_width_m(genus, dbh_in) if coefficients else None
    prediction = {
        "predictionId": prediction_id(row["chip_id"], row["output_x"], row["output_y"]),
        "xPx": x_px,
        "yPx": y_px,
        "longitude": lon,
        "latitude": lat,
        "positionRole": "crown_center",
        "centerConfidence": float(row["score"]),
        "species": species,
        "speciesConfidence": _float_or_none(row.get("species_confidence")),
        "genus": genus,
        "genusConfidence": _float_or_none(row.get("genus_confidence")),
        "dbhInches": dbh_in,
        "crownWidthM": crown[0] if crown else None,
        "crownWidthMethod": CROWN_WIDTH_METHOD if crown else None,
        "crownFit": (
            {"level": crown[1].level, "taxon": crown[1].taxon, "n": crown[1].n} if crown else None
        ),
        "candidateTreeIds": candidate_tree_ids(lon, lat, inventory, candidate_radius_m),
    }
    top = row.get("species_top_ids")
    if top is not None:
        prediction["speciesTop"] = [taxon(i, species_names) for i in list(top)][:5]
    return prediction


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def validate_bundle(bundle: dict[str, Any]) -> None:
    """The reviewer refuses a bundle this would refuse; keep the two in step
    with ``assertBundle`` in reviewer/satellite.ts."""
    required = (
        "schemaVersion", "tileId", "city", "imageryVersion", "provider",
        "acquisitionStart", "acquisitionEnd", "sourceItemIds", "image",
        "predictionLayer", "inventoryVersion", "inventoryTrees",
    )
    missing = [k for k in required if k not in bundle]
    if missing:
        raise ValueError(f"bundle {bundle.get('tileId')!r} is missing {missing}")
    if bundle["schemaVersion"] != SCHEMA_VERSION:
        raise ValueError(f"bundle schema version {bundle['schemaVersion']!r} is not {SCHEMA_VERSION}")
    image = bundle["image"]
    for k in ("url", "sha256", "width", "height", "resolutionM", "crs", "affine", "pixelToLonLat"):
        if k not in image:
            raise ValueError(f"bundle image is missing {k!r}")
    if len(image["affine"]) != 6:
        raise ValueError("image.affine must have six coefficients (a, b, c, d, e, f)")
    layer = bundle["predictionLayer"]
    if layer["status"] not in ("available", "unavailable"):
        raise ValueError(f"predictionLayer.status {layer['status']!r}")
    ids = [p["predictionId"] for p in layer.get("predictions", [])]
    if len(ids) != len(set(ids)):
        raise ValueError("prediction ids repeat within one tile")
    for tree in bundle["inventoryTrees"]:
        if tree.get("positionRole") not in ("trunk", "unknown", "crown_center"):
            raise ValueError(f"inventory tree {tree.get('treeId')!r} has no positionRole")


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------


def read_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def acquisition_window(mosaic_manifest: dict[str, Any], raster_dir: Path) -> tuple[str, str, list[str]]:
    """Calendar dates of the selected sources, from their own manifests."""
    catalog_path = raster_dir.parent / "stac-items.json"
    catalog: dict[str, Any] = {}
    if catalog_path.exists():
        catalog = {item["id"]: item for item in read_json(catalog_path).get("items", [])}
    dates: list[str] = []
    item_ids: list[str] = []
    for source in mosaic_manifest.get("sources") or []:
        item_id = str(source.get("item_id"))
        item_ids.append(item_id)
        acquired = source.get("acquisition_datetime")
        sidecar = raster_dir / f"{item_id}.manifest.json"
        if not acquired and sidecar.exists():
            acquired = read_json(sidecar).get("acquisition_datetime")
        if not acquired:
            acquired = catalog.get(item_id, {}).get("datetime") or catalog.get(item_id, {}).get(
                "properties", {}
            ).get("datetime")
        if not acquired:
            raise ValueError(f"no acquisition date for source {item_id}; refusing to date the tile")
        dates.append(date.fromisoformat(str(acquired)[:10]).isoformat())
    if not dates:
        raise ValueError("mosaic manifest lists no sources")
    return min(dates), max(dates), item_ids


def load_predictions(
    evaluation_dir: Path, chip_ids: set[str], *, all_detections: bool = False
) -> dict[str, list[dict[str, Any]]]:
    """Predictions per chip.  The export holds every candidate cell the decoder
    kept (``max_detections_per_chip``, 512 today), most of them far below the
    run's confidence threshold; only the ones above it are detections, and
    only those are exported unless *all_detections* asks for the rest."""
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    table = pq.read_table(evaluation_dir / "predictions.parquet")
    if chip_ids:
        table = table.filter(pc.is_in(table["chip_id"], value_set=pa.array(sorted(chip_ids))))
    if not all_detections and "above_threshold" in table.column_names:
        table = table.filter(pc.equal(table["above_threshold"], True))
    by_chip: dict[str, list[dict[str, Any]]] = {}
    for row in table.to_pylist():
        by_chip.setdefault(row["chip_id"], []).append(row)
    return by_chip


def load_chips(chips_dir: Path) -> dict[str, dict[str, Any]]:
    import pyarrow.parquet as pq

    table = pq.read_table(chips_dir / "chips.parquet")
    return {row["chip_id"]: row for row in table.to_pylist()}


class InventoryReader:
    """The published city parquet joined to the public prediction parquet,
    loaded once and queried per tile bounding box."""

    def __init__(self, city_parquet: str, predictions_parquet: str | None, city: str | None = None):
        import duckdb

        self.conn = duckdb.connect()
        self.conn.execute("INSTALL httpfs; LOAD httpfs;")
        pred_join = ""
        pred_cols = "NULL AS predicted_crown_width_m, NULL AS crown_model_level, NULL AS crown_dbh_source"
        if predictions_parquet:
            # The prediction parquet is the whole rollup (10.5M rows); the
            # city predicate lets DuckDB skip the row groups of other cities.
            city_filter = f"WHERE city = '{city.upper()}'" if city else ""
            pred_join = (
                "LEFT JOIN (SELECT tree_id, predicted_crown_width_m, crown_model_level, crown_dbh_source "
                f"FROM read_parquet('{predictions_parquet}') {city_filter}) p USING (tree_id)"
            )
            pred_cols = "p.predicted_crown_width_m, p.crown_model_level, p.crown_dbh_source"
        self.conn.execute(
            f"""
            CREATE TABLE inventory AS
            SELECT t.tree_id, t.city, t.data_source, t.species, t.diameter_at_breast_height,
                   t.latitude, t.longitude, t.merged_sources, t.merged_tree_ids, {pred_cols}
            FROM read_parquet('{city_parquet}') t
            {pred_join}
            WHERE t.latitude IS NOT NULL AND t.longitude IS NOT NULL
            """
        )
        self.version = inventory_version(city_parquet)

    def within(self, bounds: dict[str, float], margin_m: float = DEFAULT_INVENTORY_MARGIN_M) -> list[dict[str, Any]]:
        lat_margin = margin_m / 111_320.0
        mid_lat = (bounds["north"] + bounds["south"]) / 2
        lon_margin = margin_m / (111_320.0 * max(math.cos(math.radians(mid_lat)), 0.1))
        rows = self.conn.execute(
            """
            SELECT tree_id, data_source, species, diameter_at_breast_height, latitude, longitude,
                   merged_sources, merged_tree_ids, predicted_crown_width_m, crown_model_level,
                   crown_dbh_source
            FROM inventory
            WHERE latitude BETWEEN ? AND ? AND longitude BETWEEN ? AND ?
            ORDER BY tree_id
            """,
            [
                bounds["south"] - lat_margin, bounds["north"] + lat_margin,
                bounds["west"] - lon_margin, bounds["east"] + lon_margin,
            ],
        ).fetchall()
        return [inventory_tree(*row) for row in rows]


def inventory_tree(
    tree_id, data_source, species, dbh, lat, lon, merged_sources, merged_tree_ids,
    crown_width, crown_level, crown_dbh_source,
) -> dict[str, Any]:
    source = str(data_source or "")
    if source.startswith("SATELLITE_"):
        role = "crown_center"
    elif source.startswith("OSM_"):
        role = "unknown"
    else:
        role = "trunk"
    return {
        "treeId": tree_id,
        "longitude": float(lon),
        "latitude": float(lat),
        "species": species,
        "dbhInches": _float_or_none(dbh),
        "source": source,
        "positionRole": role,
        "mergedSources": merged_sources,
        "mergedTreeIds": merged_tree_ids,
        "predictedCrownWidthM": _float_or_none(crown_width),
        "crownModelLevel": crown_level,
        "crownDbhSource": crown_dbh_source,
    }


def inventory_version(city_parquet: str) -> str:
    if city_parquet.startswith("http"):
        import urllib.request

        request = urllib.request.Request(city_parquet, method="HEAD")
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            stamp = response.headers.get("Last-Modified") or response.headers.get("ETag") or ""
        if stamp:
            try:
                parsed = datetime.strptime(stamp, "%a, %d %b %Y %H:%M:%S %Z").replace(tzinfo=timezone.utc)
                return parsed.isoformat()
            except ValueError:
                return stamp.strip('"')
        return "unknown"
    path = Path(city_parquet)
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()


def render_tile_png(raster_path: Path, column_offset: int, row_offset: int, size: int) -> bytes:
    """RGB bands of the chip window, as PNG bytes."""
    import numpy as np
    import rasterio
    from PIL import Image
    from rasterio.windows import Window

    with rasterio.open(raster_path) as source:
        window = Window(column_offset, row_offset, size, size)
        array = source.read([1, 2, 3], window=window, boundless=True, fill_value=0)
    if array.dtype != np.uint8:
        array = np.clip(array, 0, 255).astype(np.uint8)
    image = Image.fromarray(np.moveaxis(array, 0, -1), mode="RGB")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def raster_geometry(raster_path: Path) -> tuple[tuple[float, ...], str]:
    import rasterio

    with rasterio.open(raster_path) as source:
        t = source.transform
        return (t.a, t.b, t.c, t.d, t.e, t.f), str(source.crs)


def build_bundle(
    *,
    chip: dict[str, Any],
    predictions: list[dict[str, Any]] | None,
    city: str,
    version: str,
    provider: str,
    acquisition: tuple[str, str, list[str]],
    raster_affine: tuple[float, ...],
    crs: str,
    resolution_m: float,
    chip_pixels: int,
    stride: int,
    run_id: str,
    checkpoint_sha256: str,
    taxonomy_version: str,
    taxonomy: dict[str, Any],
    confidence_threshold: float,
    coefficients: CrownCoefficients | None,
    inventory: InventoryReader | None,
    png: bytes,
    image_url: str,
    cohort: str,
) -> dict[str, Any]:
    affine = tile_affine(raster_affine, int(chip["column_offset"]), int(chip["row_offset"]))
    bounds = tile_bounds_lonlat(affine, crs, chip_pixels, chip_pixels)
    trees = inventory.within(bounds) if inventory else []
    start, end, item_ids = acquisition
    layer: dict[str, Any] = {
        "runId": run_id,
        "checkpointSha256": checkpoint_sha256,
        "taxonomyVersion": taxonomy_version,
        "confidenceThreshold": confidence_threshold,
        "cohort": cohort,
        "status": "available" if predictions is not None else "unavailable",
        "predictions": [
            build_prediction(
                row, taxonomy=taxonomy, stride=stride, affine=affine, crs=crs,
                coefficients=coefficients, inventory=trees,
            )
            for row in sorted(predictions or [], key=lambda r: -float(r["score"]))
        ],
    }
    bundle = {
        "schemaVersion": SCHEMA_VERSION,
        "tileId": tile_id(city, version, chip["chip_id"]),
        "city": city.upper(),
        "chipId": chip["chip_id"],
        "imageryVersion": version,
        "provider": provider,
        "acquisitionStart": start,
        "acquisitionEnd": end,
        "sourceItemIds": item_ids,
        "attribution": "USDA NAIP aerial imagery (public domain)",
        "image": {
            "url": image_url,
            "sha256": hashlib.sha256(png).hexdigest(),
            "width": chip_pixels,
            "height": chip_pixels,
            "resolutionM": resolution_m,
            "crs": crs,
            "affine": affine,
            "affineConvention": "rasterio (a, b, c, d, e, f): x = a*col + b*row + c; y = d*col + e*row + f; tile-local pixel, top-left corner",
            "pixelToLonLat": pixel_lonlat_affine(affine, crs, chip_pixels, chip_pixels),
            "bounds": bounds,
        },
        "predictionLayer": layer,
        "inventoryVersion": inventory.version if inventory else "none",
        "inventoryTrees": trees,
        "exportedAt": datetime.now(timezone.utc).isoformat(),
    }
    validate_bundle(bundle)
    return bundle


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def checkpoint_digest(metadata: dict[str, Any], run_dir: Path) -> str:
    """sha256 of the checkpoint file when it is on disk, else the path's."""
    checkpoint = str(metadata.get("checkpoint") or "")
    local = run_dir / "checkpoints" / Path(checkpoint).name
    if checkpoint and local.exists():
        return hashlib.sha256(local.read_bytes()).hexdigest()
    return "path:" + hashlib.sha256(checkpoint.encode("utf-8")).hexdigest()[:16]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--artifacts", default="artifacts", help="artifacts root (default: ./artifacts)")
    parser.add_argument("--run", required=True, help="training run id, e.g. sf-boston-naip-curation-v5-retrain")
    parser.add_argument("--cohort", required=True, help="evaluation cohort directory, e.g. validation or validation-usbos")
    parser.add_argument("--chips", nargs="*", default=[], help="chip ids to export; default: every chip with predictions")
    parser.add_argument("--limit", type=int, default=None, help="export at most this many chips")
    parser.add_argument("--raster", default=None, help="mosaic VRT; default: artifacts/imagery/<city>/<year>/<city>-<year>-mosaic.vrt")
    parser.add_argument("--city-parquet", default=None, help="published city parquet URL or path (default: the run's inventory parquet_url)")
    parser.add_argument("--predictions-parquet", default="https://storage.googleapis.com/trilogy_public_models/duckdb/trees/tree_predictions_v2.parquet")
    parser.add_argument("--no-inventory", action="store_true", help="skip the inventory overlay (offline)")
    parser.add_argument("--crown-coefficients", default=None, help="data/raw/crown_width_coefficients.csv")
    parser.add_argument("--out", required=True, help="output directory; one <tileId>.json and <tileId>.png per chip")
    parser.add_argument("--allow-test", action="store_true", help="export sealed test-split chips (never for the public reviewer)")
    parser.add_argument("--all-detections", action="store_true", help="include candidate cells below the run's confidence threshold")
    args = parser.parse_args(argv)

    artifacts = Path(args.artifacts)
    run_dir = artifacts / "runs" / args.run
    evaluation_dir = run_dir / "evaluation" / args.cohort
    metadata = read_json(evaluation_dir / "evaluation-metadata.json")
    taxonomy = read_json(evaluation_dir / "taxonomy.json")
    config = metadata["config"]
    city = str(metadata.get("city") or config["inventory"]["city"]).upper()
    dataset = config["dataset"]
    chips_dir = artifacts / "chips" / dataset
    if not chips_dir.exists():
        raise SystemExit(f"chip manifest not found: {chips_dir}")

    if args.raster:
        raster_path = Path(args.raster)
    else:
        configured = Path(str(config["imagery"].get("local_raster") or ""))
        candidates = sorted((artifacts / "imagery" / city.lower()).glob("*/*.vrt"))
        raster_path = next((c for c in candidates if c.name == configured.name), None) or (
            candidates[-1] if candidates else configured
        )
    if not raster_path.exists():
        raise SystemExit(f"mosaic not found: {raster_path}; pass --raster")
    mosaic_manifest = read_json(raster_path.with_suffix(".manifest.json"))
    version = imagery_version(mosaic_manifest)
    acquisition = acquisition_window(mosaic_manifest, raster_path.parent)
    raster_affine, crs = raster_geometry(raster_path)

    coefficients = None
    coefficient_path = Path(args.crown_coefficients) if args.crown_coefficients else (
        Path(__file__).resolve().parents[3] / "data" / "raw" / "crown_width_coefficients.csv"
    )
    if coefficient_path.exists():
        coefficients = CrownCoefficients.from_csv(coefficient_path)
    else:
        print(f"crown coefficients not found at {coefficient_path}; predictions get no crown width", file=sys.stderr)

    inventory = None
    if not args.no_inventory:
        city_parquet = args.city_parquet or config["inventory"]["parquet_url"]
        inventory = InventoryReader(city_parquet, args.predictions_parquet, city)

    chips = load_chips(chips_dir)
    wanted = set(args.chips)
    by_chip = load_predictions(evaluation_dir, wanted, all_detections=args.all_detections)
    chip_ids = sorted(wanted or by_chip)
    exported = 0
    skipped_test = 0
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stride = int(config["targets"]["output_stride"])
    chip_pixels = int(config["imagery"]["chip_pixels"])
    resolution_m = float(config["imagery"]["resolution_m"])
    checkpoint = checkpoint_digest(metadata, run_dir)
    taxonomy_version = hashlib.sha256(
        json.dumps(taxonomy, sort_keys=True).encode("utf-8")
    ).hexdigest()[:12]
    for chip_id in chip_ids:
        if args.limit is not None and exported >= args.limit:
            break
        chip = chips.get(chip_id)
        if chip is None:
            print(f"{chip_id}: not in the chip manifest; skipped", file=sys.stderr)
            continue
        if chip.get("split") == "test" and not args.allow_test:
            skipped_test += 1
            continue
        tid = tile_id(city, version, chip_id)
        png = render_tile_png(raster_path, int(chip["column_offset"]), int(chip["row_offset"]), chip_pixels)
        bundle = build_bundle(
            chip=chip,
            predictions=by_chip.get(chip_id),
            city=city,
            version=version,
            provider=str(config["imagery"].get("collection") or "naip"),
            acquisition=acquisition,
            raster_affine=raster_affine,
            crs=crs,
            resolution_m=resolution_m,
            chip_pixels=chip_pixels,
            stride=stride,
            run_id=args.run,
            checkpoint_sha256=checkpoint,
            taxonomy_version=taxonomy_version,
            taxonomy=taxonomy,
            confidence_threshold=float(config["evaluation"]["confidence_threshold"]),
            coefficients=coefficients,
            inventory=inventory,
            png=png,
            image_url=f"{tid}.png",
            cohort=args.cohort,
        )
        (out / f"{tid}.png").write_bytes(png)
        (out / f"{tid}.json").write_text(json.dumps(bundle, indent=1), encoding="utf-8")
        exported += 1
        n = len(bundle["predictionLayer"]["predictions"])
        print(f"{tid}: {n} predictions, {len(bundle['inventoryTrees'])} inventory trees")
    if skipped_test:
        print(f"skipped {skipped_test} sealed test-split chip(s); pass --allow-test to export them", file=sys.stderr)
    print(f"exported {exported} tile(s) to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
