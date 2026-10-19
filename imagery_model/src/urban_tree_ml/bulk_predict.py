"""Predict a city's NAIP tiles with one run and stage the result for the
``PREDICTED_{code}`` partition (data/raw/predicted_tree_info.py).

Three steps, each a subcommand, each writing its own output so a run that
dies loses at most the tile it was on:

``plan``
    Which tiles.  Every published tree of the city (any source but an earlier
    prediction) marks the grid tile it falls in, the marks are dilated by one
    tile in every direction, and tiles whose centre is outside the city's
    territory are dropped.  That covers the streets, yards and parks around
    the inventory and skips open water and the airport, without a boundary
    file.  The grid is full-tile steps on point_predict's half-tile lattice in
    the imagery's UTM zone, so each planned tile is exactly the tile
    ``point_predict`` (and the reviewer's "Predict at") would cut there.

``predict``
    Runs planned tiles through ``point_predict.predict_tile`` -- the
    reviewer's own fetch, decode and bundle path, so a staged row is what the
    reviewer shows for that tile -- inside a time budget, and writes one small
    JSON per tile under ``<out>/tiles``.  Run it again to continue; a tile
    already written is skipped.  NAIP windows are cached under
    ``artifacts/point-tiles`` and a cached tile is found from the plan's UTM
    zone, so predicting again with a later run neither reads imagery nor
    searches the STAC catalog (it uses the newest year cached).

``assemble``
    Keeps the detections at or above ``--threshold``, drops the second of two
    detections from *different* tiles within ``--seam-radius-m`` (a crown on
    a tile seam is seen from both sides; the merge never joins two
    predictions, so this is the only place that can), mints stable ``pred-``
    ids and writes ``<out>/{code}_predicted_staging.parquet`` in the contract
    the ingest documents.  ``--from-bundles DIR`` assembles reviewer tile
    bundles instead, for a pilot over tiles already exported.

``overview``
    Writes ``<out>/overview.json`` for the reviewer's map: every planned
    tile's footprint and state, every detection (score, genus, and whether a
    published tree would absorb it in the merge), and the city's published
    trees.  Cheap; rerun it after each ``predict`` batch.

Uploading is deliberately a separate, manual step -- it is what makes the
city's next refresh publish the rows::

    gcloud storage cp <out>/usbos_predicted_staging.parquet \\
      gs://trilogy_public_models/duckdb/staging/usbos_predicted_staging.parquet

Example (Boston, v13)::

    uv run --group imagery --group train python -m urban_tree_ml.bulk_predict plan \\
      --city USBOS --run sf-boston-nyc-naip-swin-30cm-v13-curated-finetune --out artifacts/bulk/usbos-v13
    uv run --group imagery --group train python -m urban_tree_ml.bulk_predict predict \\
      --out artifacts/bulk/usbos-v13 --max-minutes 15
    uv run --group imagery python -m urban_tree_ml.bulk_predict assemble \\
      --out artifacts/bulk/usbos-v13 --threshold 0.15

Nothing here knows about splits: these rows are published as unreviewed
predictions, and keeping them out of training labels is the training side's
job (they carry ``data_source = PREDICTED_{code}`` and ``pred-`` ids).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
from pathlib import Path
from typing import Any, Iterable

from urban_tree_ml.point_predict import INGEST_PATH, PARQUET_URL, city_boxes, owning_city

ID_PREFIX = "pred-"
MODEL_CROWN_METHOD = "model_crown_head"
STAGED_COLUMNS = (
    "tree_id", "city", "latitude", "longitude", "genus", "species", "score",
    "crown_width_m", "run_id", "imagery_version", "species_confidence", "genus_confidence",
)


# ---------------------------------------------------------------------------
# Pure pieces
# ---------------------------------------------------------------------------


def utm_epsg(lon: float) -> int:
    """NAD83 / UTM zone EPSG for a CONUS longitude -- the CRS NAIP is served in."""
    return 26900 + int(math.floor((lon + 180) / 6)) + 1


def tile_index(x: float, y: float, tile_m: float) -> tuple[int, int]:
    return int(math.floor(x / tile_m)), int(math.floor(y / tile_m))


def tile_centre(index: tuple[int, int], tile_m: float) -> tuple[float, float]:
    """The tile's centre; an odd multiple of half a tile, so point_predict cuts exactly this tile."""
    i, j = index
    return (i + 0.5) * tile_m, (j + 0.5) * tile_m


def dilate(indices: Iterable[tuple[int, int]], rings: int = 1) -> set[tuple[int, int]]:
    out: set[tuple[int, int]] = set()
    for i, j in indices:
        for di in range(-rings, rings + 1):
            for dj in range(-rings, rings + 1):
                out.add((i + di, j + dj))
    return out


def prediction_id(run_id: str, imagery_version: str, chip_id: str, prediction: str) -> str:
    digest = hashlib.sha1(f"{run_id}|{imagery_version}|{chip_id}|{prediction}".encode("utf-8")).hexdigest()
    return f"{ID_PREFIX}{digest[:20]}"


def rows_from_bundle(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    """Every detection of one tile bundle, as staged-file rows plus the tile it came from."""
    layer = bundle["predictionLayer"]
    run_id, version, chip = str(layer["runId"]), str(bundle["imageryVersion"]), str(bundle["chipId"])
    rows = []
    for p in layer.get("predictions") or []:
        crown = p.get("modelCrownWidthM") if p.get("modelCrownWidthMethod") == MODEL_CROWN_METHOD else None
        rows.append({
            "tree_id": prediction_id(run_id, version, chip, str(p["predictionId"])),
            "city": str(bundle["city"]).upper(),
            "latitude": float(p["latitude"]),
            "longitude": float(p["longitude"]),
            "genus": p.get("genus"),
            "species": p.get("species"),
            "score": float(p["centerConfidence"]),
            "crown_width_m": float(crown) if crown else None,
            "species_confidence": _float_or_none(p.get("speciesConfidence")),
            "genus_confidence": _float_or_none(p.get("genusConfidence")),
            "run_id": run_id,
            "imagery_version": version,
            "_tile": str(bundle["tileId"]),
        })
    return rows


def _float_or_none(value: Any) -> float | None:
    return None if value is None else float(value)


def drop_seam_duplicates(rows: list[dict[str, Any]], radius_m: float) -> list[dict[str, Any]]:
    """Keep the higher-scoring of two detections from different tiles within *radius_m*.

    Detections of one tile were already peak-suppressed by the model and are
    left alone; only pairs across a tile boundary are compared.
    """
    if not rows:
        return []
    lat0 = sum(r["latitude"] for r in rows) / len(rows)
    m_lat, m_lon = 111_320.0, 111_320.0 * math.cos(math.radians(lat0))
    cells: dict[tuple[int, int], list[int]] = {}
    kept: list[dict[str, Any]] = []
    for row in sorted(rows, key=lambda r: -r["score"]):
        x, y = row["longitude"] * m_lon, row["latitude"] * m_lat
        ci, cj = int(math.floor(x / radius_m)), int(math.floor(y / radius_m))
        clash = False
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                for k in cells.get((ci + di, cj + dj), ()):
                    other = kept[k]
                    if other["_tile"] == row["_tile"]:
                        continue
                    ox, oy = other["longitude"] * m_lon, other["latitude"] * m_lat
                    if math.hypot(x - ox, y - oy) <= radius_m:
                        clash = True
                        break
                if clash:
                    break
            if clash:
                break
        if not clash:
            cells.setdefault((ci, cj), []).append(len(kept))
            kept.append(row)
    return kept


def dedup_cells(lat: float, lon: float, cell_lat_deg: float, cell_lon_deg: float) -> list[tuple[str, int, int]]:
    """The four half-cell-staggered merge cells of a point (tree_dedup.preql's
    ``cell_a``..``cell_d``): two rows sharing any one are merge candidates."""
    return [
        (name, math.floor(lon / cell_lon_deg + dx), math.floor(lat / cell_lat_deg + dy))
        for name, dx, dy in (("a", 0, 0), ("b", 0.5, 0), ("c", 0, 0.5), ("d", 0.5, 0.5))
    ]


def absorbed_flags(
    predictions: list[tuple[float, float]], trees: list[tuple[float, float]],
    cell_lat_deg: float, cell_lon_deg: float,
) -> list[bool]:
    """Whether each (lat, lon) prediction shares a merge cell with a published
    tree -- the merge then folds it into that tree and it never shows as its
    own.  Published positions stand in for the raw ones the build keys on."""
    occupied = {c for lat, lon in trees for c in dedup_cells(lat, lon, cell_lat_deg, cell_lon_deg)}
    return [
        any(c in occupied for c in dedup_cells(lat, lon, cell_lat_deg, cell_lon_deg))
        for lat, lon in predictions
    ]


def cell_degrees(city: str, ingest_path: Path = INGEST_PATH) -> tuple[float, float]:
    """(lat, lon) degrees of the city's merge cell, as shared/ingest.py's
    cell_degrees computes them; the literals are read, not imported."""
    import ast

    found: dict[str, Any] = {}
    for node in ast.parse(ingest_path.read_text(encoding="utf-8")).body:
        target = getattr(node, "target", None) or (getattr(node, "targets", None) or [None])[0]
        if getattr(target, "id", None) in ("DEDUP_CELL_METRES", "CITY_BOUNDS") and node.value is not None:
            found[target.id] = ast.literal_eval(node.value)
    metres = found["DEDUP_CELL_METRES"][city]
    lat_min, lat_max, _, _ = found["CITY_BOUNDS"][city]
    lat = math.radians((lat_min + lat_max) / 2)
    return metres / 111320.0, metres / (111320.0 * math.cos(lat))


def staged_table(rows: list[dict[str, Any]]):
    import pyarrow as pa

    schema = pa.schema([
        ("tree_id", pa.string()), ("city", pa.string()), ("latitude", pa.float64()),
        ("longitude", pa.float64()), ("genus", pa.string()), ("species", pa.string()),
        ("score", pa.float64()), ("crown_width_m", pa.float64()), ("run_id", pa.string()),
        ("imagery_version", pa.string()), ("species_confidence", pa.float64()),
        ("genus_confidence", pa.float64()),
    ])
    return pa.Table.from_pylist([{c: r[c] for c in STAGED_COLUMNS} for r in rows], schema=schema)


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------


def run_geometry(artifacts: Path, run: str, city: str) -> tuple[float, float]:
    """(tile_m, run threshold) from the run's own config, without loading the model."""
    config_path = artifacts / "runs" / run / f"config-{city.lower()}.json"
    if not config_path.exists():
        raise SystemExit(f"{config_path} not found; is {run} a run with a {city} config?")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    imagery = config["imagery"]
    return int(imagery["chip_pixels"]) * float(imagery["resolution_m"]), float(config["evaluation"]["confidence_threshold"])


def published_points(city: str) -> list[tuple[float, float]]:
    import duckdb

    url = PARQUET_URL.format(code=city.lower())
    return duckdb.execute(
        f"select latitude, longitude from read_parquet('{url}') "
        "where latitude is not null and longitude is not null and data_source not like 'PREDICTED_%'"
    ).fetchall()


def plan(args: argparse.Namespace) -> int:
    from pyproj import Transformer

    city = args.city.upper()
    artifacts = Path(args.artifacts)
    tile_m, run_threshold = run_geometry(artifacts, args.run, city)
    points = published_points(city)
    if not points:
        raise SystemExit(f"{city} has no published trees to plan around")
    envelopes, territory = city_boxes()
    lon_mid = sorted(p[1] for p in points)[len(points) // 2]
    epsg = utm_epsg(lon_mid)
    forward = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)
    back = Transformer.from_crs(f"EPSG:{epsg}", "EPSG:4326", always_xy=True)
    xs, ys = forward.transform([p[1] for p in points], [p[0] for p in points])
    marked = {tile_index(x, y, tile_m) for x, y in zip(xs, ys)}
    tiles = []
    for index in sorted(dilate(marked, args.rings)):
        cx, cy = tile_centre(index, tile_m)
        lon, lat = back.transform(cx, cy)
        if owning_city(lat, lon, envelopes, territory) == city:
            tiles.append({"i": index[0], "j": index[1], "lat": lat, "lon": lon})
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    manifest = {
        "city": city, "run": args.run, "epsg": epsg, "tileM": tile_m, "rings": args.rings,
        "runThreshold": run_threshold, "publishedTrees": len(points),
        "markedTiles": len(marked), "tiles": tiles,
    }
    (out / "plan.json").write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    area_km2 = len(tiles) * tile_m * tile_m / 1e6
    print(f"{city}: {len(tiles)} tiles (~{area_km2:.0f} km2) around {len(points)} published trees; "
          f"at ~6 s/tile about {len(tiles) * 6 / 3600:.1f} h", file=sys.stderr)
    return 0


def tile_path(out: Path, tile: dict[str, Any]) -> Path:
    return out / "tiles" / f"{tile['i']}_{tile['j']}.json"


def predict(args: argparse.Namespace) -> int:
    from urban_tree_ml.point_predict import Predictor, predict_tile

    out = Path(args.out)
    manifest = json.loads((out / "plan.json").read_text(encoding="utf-8"))
    city, run = manifest["city"], manifest["run"]
    artifacts = Path(args.artifacts)
    (out / "tiles").mkdir(parents=True, exist_ok=True)

    def pending(tile: dict[str, Any]) -> bool:
        path = tile_path(out, tile)
        if not path.exists():
            return True
        return args.retry_errors and json.loads(path.read_text(encoding="utf-8")).get("status") == "error"

    todo = [t for t in manifest["tiles"] if pending(t)]
    total = len(manifest["tiles"])
    print(f"{total - len(todo)}/{total} tiles done; {len(todo)} to go", file=sys.stderr)
    if args.near:
        # Nearest first, so a limited batch is one contiguous block, not a strip
        # along the plan's (i, j) order.
        lat0, lon0 = (float(v) for v in args.near.split(","))
        scale = math.cos(math.radians(lat0))
        todo.sort(key=lambda t: (t["lat"] - lat0) ** 2 + ((t["lon"] - lon0) * scale) ** 2)
    if args.limit:
        todo = todo[: args.limit]
    if not todo:
        return 0
    predictor = Predictor(artifacts, run, city, args.device)
    stac_url = str(predictor.config["imagery"].get("stac_url") or "https://planetarycomputer.microsoft.com/api/stac/v1")
    envelopes, territory = city_boxes()
    deadline = time.monotonic() + args.max_minutes * 60
    done = 0
    for tile in todo:
        if time.monotonic() > deadline:
            break
        path = tile_path(out, tile)
        try:
            result = predict_tile(
                predictor, tile["lat"], tile["lon"], artifacts=artifacts, run=run, year=args.year,
                stac_url=stac_url, city=city, envelopes=envelopes, territory=territory,
                min_score=args.min_score, threshold=manifest["runThreshold"], coefficients=None, readers=None,
                epsg_hint=int(manifest["epsg"]),
            )
        except Exception as error:  # one bad tile must not end the batch
            record = {"status": "error", "error": f"{error.__class__.__name__}: {error}"}
            print(f"{path.name}: {record['error']}", file=sys.stderr)
        else:
            if result is None:
                record = {"status": "skipped"}
            else:
                bundle = result["bundle"]
                # The bundle minus its imagery and inventory: what assemble reads.
                record = {
                    "status": "ok",
                    "tileId": bundle["tileId"], "chipId": bundle["chipId"], "city": bundle["city"],
                    "imageryVersion": bundle["imageryVersion"],
                    "predictionLayer": {
                        "runId": bundle["predictionLayer"]["runId"],
                        "predictions": [
                            {k: p.get(k) for k in (
                                "predictionId", "latitude", "longitude", "genus", "species",
                                "speciesConfidence", "genusConfidence",
                                "centerConfidence", "modelCrownWidthM", "modelCrownWidthMethod",
                            )}
                            for p in bundle["predictionLayer"]["predictions"]
                        ],
                    },
                }
        path.write_text(json.dumps(record) + "\n", encoding="utf-8")
        done += 1
    remaining = len(todo) - done
    print(f"predicted {done} tiles this batch; {remaining} still to go"
          + (" (rerun to continue)" if remaining else ""), file=sys.stderr)
    return 0


def assemble(args: argparse.Namespace) -> int:
    import pyarrow.parquet as pq

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    if args.from_bundles:
        sources = sorted(Path(args.from_bundles).rglob("*.json"))
        bundles = [json.loads(p.read_text(encoding="utf-8")) for p in sources]
        bundles = [b for b in bundles if isinstance(b, dict) and "predictionLayer" in b]
    else:
        bundles = []
        for path in sorted((out / "tiles").glob("*.json")):
            record = json.loads(path.read_text(encoding="utf-8"))
            if record.get("status") == "ok":
                bundles.append(record)
    city = (args.city or (bundles[0]["city"] if bundles else "")).upper()
    runs = {b["predictionLayer"]["runId"] for b in bundles}
    if len(runs) > 1:
        raise SystemExit(f"bundles from {len(runs)} runs ({', '.join(sorted(runs))}); assemble one run at a time")
    envelopes, territory = city_boxes()
    rows = [
        r for b in bundles if str(b["city"]).upper() == city for r in rows_from_bundle(b)
        if r["score"] >= args.threshold
    ]
    rows = [r for r in rows if owning_city(r["latitude"], r["longitude"], envelopes, territory) == city]
    kept = drop_seam_duplicates(rows, args.seam_radius_m)
    target = out / f"{city.lower()}_predicted_staging.parquet"
    pq.write_table(staged_table(kept), target)
    print(f"{city}: {len(bundles)} tiles, {len(rows)} detections >= {args.threshold}, "
          f"{len(rows) - len(kept)} seam duplicates dropped, {len(kept)} rows -> {target}", file=sys.stderr)
    return 0


def overview(args: argparse.Namespace) -> int:
    import duckdb
    from pyproj import Transformer

    out = Path(args.out)
    manifest = json.loads((out / "plan.json").read_text(encoding="utf-8"))
    city, tile_m = manifest["city"], float(manifest["tileM"])
    back = Transformer.from_crs(f"EPSG:{manifest['epsg']}", "EPSG:4326", always_xy=True)

    tiles, detections = [], []
    for k, tile in enumerate(manifest["tiles"]):
        i, j = tile["i"], tile["j"]
        xs = [i * tile_m, (i + 1) * tile_m, (i + 1) * tile_m, i * tile_m]
        ys = [(j + 1) * tile_m, (j + 1) * tile_m, j * tile_m, j * tile_m]
        lons, lats = back.transform(xs, ys)
        path = tile_path(out, tile)
        record = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"status": "pending"}
        tiles.append({
            "key": f"{i}_{j}", "lat": round(tile["lat"], 6), "lon": round(tile["lon"], 6),
            # NW, NE, SE, SW: the corner order MapLibre's image sources use.
            "footprint": [[round(x, 6), round(y, 6)] for x, y in zip(lons, lats)],
            "status": record["status"], "tileId": record.get("tileId"),
        })
        for p in (record.get("predictionLayer") or {}).get("predictions") or []:
            detections.append((k, p))

    url = PARQUET_URL.format(code=city.lower())
    published = duckdb.execute(
        f"select latitude, longitude, data_source from read_parquet('{url}') "
        "where latitude is not null and longitude is not null and data_source not like 'PREDICTED_%'"
    ).fetchall()
    cell_lat, cell_lon = cell_degrees(city)
    flags = absorbed_flags(
        [(p["latitude"], p["longitude"]) for _, p in detections],
        [(lat, lon) for lat, lon, _ in published], cell_lat, cell_lon,
    )

    genera: dict[str, int] = {}
    sources: dict[str, int] = {}
    doc = {
        "city": city, "run": manifest["run"], "tileM": tile_m,
        "dedupCellM": round(cell_lat * 111320.0, 3),
        "tiles": tiles,
        # Column arrays: tens of thousands of points stay a few MB.
        "predictions": {"tile": [], "lon": [], "lat": [], "score": [], "genus": [], "absorbed": []},
        "genera": [],
        "trees": {"lon": [], "lat": [], "source": []},
        "sources": [],
    }
    cols = doc["predictions"]
    for (k, p), absorbed in zip(detections, flags):
        genus = p.get("genus") or ""
        cols["tile"].append(k)
        cols["lon"].append(round(p["longitude"], 6))
        cols["lat"].append(round(p["latitude"], 6))
        cols["score"].append(round(float(p["centerConfidence"]), 3))
        cols["genus"].append(genera.setdefault(genus, len(genera)))
        cols["absorbed"].append(1 if absorbed else 0)
    for lat, lon, source in published:
        doc["trees"]["lon"].append(round(lon, 6))
        doc["trees"]["lat"].append(round(lat, 6))
        doc["trees"]["source"].append(sources.setdefault(source, len(sources)))
    doc["genera"] = list(genera)
    doc["sources"] = list(sources)
    target = out / "overview.json"
    target.write_text(json.dumps(doc, separators=(",", ":")) + "\n", encoding="utf-8")
    done = sum(t["status"] == "ok" for t in tiles)
    print(f"{city}: {done}/{len(tiles)} tiles predicted, {len(detections)} detections "
          f"({sum(flags)} absorbed by a published tree), {len(published)} published trees -> {target}",
          file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--artifacts", default="artifacts", help="artifacts root (default: ./artifacts)")
    sub = parser.add_subparsers(dest="step", required=True)

    p = sub.add_parser("plan", help="choose the city's tiles")
    p.add_argument("--city", required=True)
    p.add_argument("--run", required=True, help="training run id")
    p.add_argument("--rings", type=int, default=1, help="tiles of margin around the published trees (default 1)")
    p.add_argument("--out", required=True)

    p = sub.add_parser("predict", help="predict planned tiles within a time budget; rerun to continue")
    p.add_argument("--out", required=True)
    p.add_argument("--max-minutes", type=float, default=15.0)
    p.add_argument("--limit", type=int, default=None, help="at most this many tiles this batch")
    p.add_argument("--near", default=None, metavar="LAT,LON", help="predict the tiles nearest this point first")
    p.add_argument("--min-score", type=float, default=0.1, help="keep candidates scoring at least this (default 0.1)")
    p.add_argument("--year", type=int, default=None)
    p.add_argument("--device", default="auto")
    p.add_argument("--retry-errors", action="store_true")

    p = sub.add_parser("assemble", help="threshold, seam dedup and write the staged parquet")
    p.add_argument("--out", required=True)
    p.add_argument("--threshold", type=float, required=True, help="publish detections scoring at least this")
    p.add_argument("--seam-radius-m", type=float, default=2.0)
    p.add_argument("--city", default=None)
    p.add_argument("--from-bundles", default=None, help="assemble reviewer tile bundles under this directory instead")

    p = sub.add_parser("overview", help="write overview.json for the reviewer's map")
    p.add_argument("--out", required=True)

    args = parser.parse_args(argv)
    return {"plan": plan, "predict": predict, "assemble": assemble, "overview": overview}[args.step](args)


if __name__ == "__main__":
    raise SystemExit(main())
