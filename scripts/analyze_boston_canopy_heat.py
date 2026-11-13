#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = ["duckdb", "matplotlib", "numpy", "requests", "scipy", "shapely>=2.1", "rasterio", "pyproj"]
# ///
"""Compare crown-union area and tree density against the same summer temperatures.

Separate target from the frozen income poster. Reuses its cached tree snapshot,
geographies and five-date heat measurements, but imposes no income filter.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import duckdb
import numpy as np
from pyproj import Transformer
from rasterio.features import rasterize
from rasterio.transform import from_origin
from scipy import stats
import shapely
from shapely.geometry import box

from boston_income_heat_data import HERE, CACHE, save_json, session, tree_snapshot
from render_boston_income_heat import context
from render_boston_sources_map import load_points, load_canopy_widths, DEFAULT_CROWNS_INPUT

OUT = HERE / "boston_canopy_renders"
BASE = HERE / "boston_renders" / "boston_income_tree_heat_correlations.json"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def crown_snapshot():
    path = CACHE / "usbos-crown-predictions.parquet"
    if not path.exists():
        print("Caching crown-size predictions ...", flush=True)
        with session() as client, client.get(DEFAULT_CROWNS_INPUT, stream=True, timeout=(20, 180)) as response:
            response.raise_for_status()
            pending = path.with_suffix(".pending")
            with pending.open("wb") as stream:
                for chunk in response.iter_content(1024*1024):
                    stream.write(chunk)
            pending.replace(path)
            save_json(path.with_suffix(".source.json"), {
                "url": DEFAULT_CROWNS_INPUT, "downloaded_utc": datetime.now(timezone.utc).isoformat(),
                "last_modified": response.headers.get("Last-Modified"), "etag": response.headers.get("ETag")})
    return path


def crown_cover(land, centers, radii, resolution=2):
    """Raster union, clipped to land, including crowns with centers outside it.

    Coordinates and radii must be metres in the same projected CRS. Pixel-center
    sampling, not all_touched, avoids systematically enlarging small crowns.
    Empty/missing crowns are not imputed. Caller supplies all intersecting crowns.
    """
    xmin, ymin, xmax, ymax = land.bounds
    xmin, ymin = np.floor(np.array([xmin, ymin])/resolution)*resolution
    xmax, ymax = np.ceil(np.array([xmax, ymax])/resolution)*resolution
    transform = from_origin(xmin, ymax, resolution, resolution)
    shape = (int(round((ymax-ymin)/resolution)), int(round((xmax-xmin)/resolution)))
    land_pixels = rasterize([(land, 1)], out_shape=shape, transform=transform, dtype="uint8").astype(bool)
    valid = np.isfinite(radii) & (radii > 0)
    if not valid.any():
        return 0., int(land_pixels.sum())*resolution**2
    circles = shapely.buffer(centers[valid], radii[valid], quad_segs=16)
    coverage = rasterize([(c, 1) for c in circles], out_shape=shape, transform=transform,
                         dtype="uint8").astype(bool)
    return int((coverage & land_pixels).sum())*resolution**2, int(land_pixels.sum())*resolution**2


def association(rows, metric, temperature="temperature"):
    x = np.array([r[metric] for r in rows])
    y = np.array([r[temperature] for r in rows])
    return {"n": len(rows), "spearman": float(stats.spearmanr(x, y).statistic),
            "pearson": float(stats.pearsonr(x, y).statistic)}


def comparison(rows):
    return {m: association(rows, m) for m in ("tree_density", "canopy_percent", "summed_crown_percent")}


def paired_bootstrap(rows, repeats=1000, tile_km=3):
    """Paired spatial resampling: compare magnitudes on exactly the same draws."""
    xy = np.array([[r["x_km"], r["y_km"]] for r in rows])
    _, labels = np.unique(np.floor(xy/tile_km).astype(int), axis=0, return_inverse=True)
    members = [np.flatnonzero(labels == i) for i in range(labels.max()+1)]
    values = np.array([[r["tree_density"], r["canopy_percent"], r["temperature"]] for r in rows])
    rng = np.random.default_rng(20261112)
    draws = []
    for _ in range(repeats):
        take = np.concatenate([members[i] for i in rng.integers(0, len(members), len(members))])
        sample = values[take]
        count = stats.spearmanr(sample[:, 0], sample[:, 2]).statistic
        canopy = stats.spearmanr(sample[:, 1], sample[:, 2]).statistic
        draws.append([count, canopy, abs(canopy)-abs(count)])
    return {"tile_km": tile_km, "tiles": len(members), "replicates": repeats,
            "intervals_95": dict(zip(("count_rho", "canopy_rho", "canopy_minus_count_absolute_rho"),
                                      np.quantile(draws, [.025, .975], axis=0).T.tolist())),
            "interpretation": "Spatial sampling sensitivity; not measurement-error or causal uncertainty"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resolution", type=float, default=2)
    parser.add_argument("--bootstrap", type=int, default=1000)
    parser.add_argument("--recompute", action="store_true")
    args = parser.parse_args()
    if args.resolution <= 0 or args.bootstrap < 100:
        parser.error("Positive resolution and at least 100 bootstrap draws required")
    OUT.mkdir(parents=True, exist_ok=True)
    base = json.loads(BASE.read_text())
    base_rows_path = BASE.with_name(BASE.stem+"_block_groups.json")
    base_rows = json.loads(base_rows_path.read_text())
    tree_path = tree_snapshot()
    if sha(tree_path) != base["provenance"]["tree_sha256"]:
        raise ValueError("Tree snapshot differs from frozen analysis")
    prediction_path = crown_snapshot()
    fingerprint = {"base_sha256": sha(BASE), "base_rows_sha256": sha(base_rows_path),
                   "tree_sha256": sha(tree_path), "crown_sha256": sha(prediction_path),
                   "resolution_m": args.resolution, "algorithm": "circle-union-land-v1"}
    cache_path = CACHE / ("canopy-heat-"+hashlib.sha256(json.dumps(fingerprint, sort_keys=True).encode()).hexdigest()[:12]+".json")
    if cache_path.exists() and not args.recompute:
        cache = json.loads(cache_path.read_text())
        rows, crown_meta = cache["rows"], cache["crowns"]
    else:
        _, _, _, _, water_meta, groups = context()
        if water_meta["cache_sha256"] != base["provenance"]["water"]["cache_sha256"]:
            raise ValueError("Water differs from frozen analysis")
        by_id = {g["id"]: g for g in groups}
        points, _ = load_points(str(tree_path))
        widths, crown_meta = load_canopy_widths(points, np.ones(len(points["tree_id"]), dtype=bool), str(prediction_path))
        if np.nanmax(widths) > 100:
            raise ValueError("Crown diameter above 100 m: inspect source rather than silently clamp")
        project = Transformer.from_crs(4326, 26986, always_xy=True)
        east, north = project.transform(points["longitude"], points["latitude"])
        centers = shapely.points(east, north)
        index = shapely.STRtree(centers)
        radii = widths/2
        max_radius = float(np.nanmax(radii))
        rows = []
        # No household, population or income condition: parks/industrial land stay.
        candidates = [r for r in base_rows if r["land_km2"] >= .03 and r["thermal_cells"] >= 30
                      and min(r["daily_coverage"]) >= .9 and np.isfinite(r["temperature"])]
        for n, original in enumerate(candidates):
            land = by_id[original["id"]]["area"]
            xmin, ymin, xmax, ymax = land.bounds
            nearby = index.query(box(xmin-max_radius, ymin-max_radius, xmax+max_radius, ymax+max_radius))
            inside = nearby[shapely.covers(land, centers[nearby])]
            valid = np.isfinite(widths[inside]) & (widths[inside] > 0)
            source_width = np.isfinite(points["crown_width_m"][inside]) & (points["crown_width_m"][inside] > 0)
            area, raster_land = crown_cover(land, centers[nearby], radii[nearby], args.resolution)
            fine_area, fine_land = crown_cover(land, centers[nearby], radii[nearby], args.resolution/2)
            # Keep income fields out of this new target, but flag old sample membership.
            row = {k: original[k] for k in ("id", "x_km", "y_km", "land_km2", "trees", "tree_density",
                    "temperature", "daily_temperature", "daily_coverage", "thermal_cells")}
            row.update(original_poster_sample=original["eligible"], crown_width_count=int(valid.sum()),
                crown_width_fraction=float(valid.mean()) if len(valid) else 0.,
                source_width_count=int(source_width.sum()), model_width_count=int((valid & ~source_width).sum()),
                canopy_m2=area, canopy_percent=100*area/raster_land,
                fine_canopy_percent=100*fine_area/fine_land,
                summed_crown_percent=float(100*np.sum(np.pi*radii[inside][valid]**2)/land.area),
                raster_land_m2=raster_land, projected_land_m2=float(land.area))
            rows.append(row)
            if n % 25 == 0:
                print(f"Crown union {n+1}/{len(candidates)}", flush=True)
        crown_meta["by_source"] = {str(s): {"trees": int(np.sum(points["data_source"] == s)),
            "valid_widths": int(np.sum((points["data_source"] == s) & np.isfinite(widths)))}
            for s in np.unique(points["data_source"])}
        save_json(cache_path, {"rows": rows, "crowns": crown_meta, "fingerprint": fingerprint})
    sample = [r for r in rows if r["crown_width_fraction"] >= .95]
    old = [r for r in sample if r["original_poster_sample"]]
    result = {"generated_utc": datetime.now(timezone.utc).isoformat(), "fingerprint": fingerprint,
        "n": len(sample), "heat_eligible_n": len(rows), "correlations": comparison(sample),
        "original_poster_sample": comparison(old), "crowns": crown_meta,
        "width_coverage_sensitivity": {str(threshold): comparison([r for r in rows if r["crown_width_fraction"] >= threshold])
                                       for threshold in (0, .90, .95, .99, 1.)},
        "spatial_bootstrap": [paired_bootstrap(sample, args.bootstrap, size) for size in (3, 5)],
        "resolution_sensitivity": {"fine_resolution_m": args.resolution/2,
            "fine_association": association(sample, "fine_canopy_percent"),
            "max_absolute_percentage_point_change": max(abs(r["fine_canopy_percent"]-r["canopy_percent"]) for r in sample)},
        "daily_correlations": [{"date": date, **comparison([{**r, "temperature": r["daily_temperature"][i]} for r in sample])}
                               for i, date in enumerate(base["provenance"]["dates"])],
        "provenance": {"heat": base["provenance"]["temperature"], "dates": base["provenance"]["dates"],
            "water": base["provenance"]["water"], "circle": base["provenance"]["circle"],
            "tree_source": base["provenance"]["trees"], "crs": "EPSG:26986, metres",
            "geography": "Whole Census block groups >=99% inside original unfaded circle",
            "eligibility": ">=0.03 km² land; >=30 thermal cells; >=90% valid cells on every fixed date; >=95% of mapped trees with a crown width; no demographic filter",
            "canopy": "Union of source/model crown circles, rasterized with pixel-center sampling, clipped to land. Includes intersecting crowns centered outside each block group. Percent of raster land; overlaps counted once.",
            "summed_crown_area": "Sensitivity only: sum pi*(width/2)^2 for centers in land, divided by exact land area. Overlaps repeat, so this is not canopy cover.",
            "missing_widths": "Not imputed; coverage reported and groups below 95% excluded"},
        "limitations": ["Estimated crown footprint, not measured canopy or shading at the satellite overpass",
            "Source widths include aerial model outputs; missing/false trees and crown-size errors remain",
            "2026 trees/widths compared with 2025 heat; no historical planting effect is estimated",
            "Five clear-sky dates, not all-weather summer average or air temperature",
            "Spatial correlation, not causation; land use and source completeness may confound it"]}
    for row in rows:
        row["eligible"] = row["crown_width_fraction"] >= .95
    save_json(OUT/"boston_canopy_heat.json", result)
    save_json(OUT/"boston_canopy_heat_block_groups.json", rows)
    print(json.dumps({"n": result["n"], "correlations": result["correlations"],
        "original_sample": result["original_poster_sample"], "resolution": result["resolution_sensitivity"],
        "bootstrap": result["spatial_bootstrap"]}, indent=2), flush=True)


if __name__ == "__main__":
    main()
