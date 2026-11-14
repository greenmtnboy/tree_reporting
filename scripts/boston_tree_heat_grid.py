"""Equal-size Boston tree/heat cells and temperature-only landmark pairs.

No Census or income geometry is used. All measurements use a fixed 500 m
EPSG:32619 grid and the same five dates and published trees as the poster.
"""
import hashlib
import json
from pathlib import Path

from affine import Affine
import numpy as np
from pyproj import Transformer
from rasterio.features import geometry_mask
from scipy.stats import pearsonr, spearmanr
import shapely
from shapely.geometry import shape, box, mapping

from analyze_boston_canopy_heat import OUT, sha
from boston_income_heat_data import load_heat, tree_snapshot
from render_boston_income_heat import transform_geometry
from render_boston_sources_map import load_points, load_water, circle_fade_config
from treelines_data import project_lonlat

CELL_M = 500
VIEW_M = 2500
MIN_LAND = .90
MIN_HEAT = .90
# Fixed geographic anchors, chosen before inspecting the grid outcomes.
# Replaces the old block-group centroids with actual landmark-area centers.
LANDMARKS = {
    "Edgeworth, Malden": (-71.08047, 42.41633),
    "Newmarket": (-71.07007, 42.32930),
    "Nubian Square": (-71.083, 42.329),
    "Union Square": (-71.095, 42.380),
    "Egleston Square": (-71.0973, 42.3141),
    "Brookline Village": (-71.119, 42.332),
    "Harvard Square": (-71.119, 42.374),
    "Roslindale Village": (-71.129, 42.286),
    "Avon Hill": (-71.12250, 42.38583),
    "Mattapan Square": (-71.093, 42.269),
    "Corey Hill Park": (-71.133926, 42.342369),
    "Larz Anderson Park": (-71.13611, 42.31194),
}
ANCHOR_SOURCES = {
    "Corey Hill Park": "https://spot.brooklinema.gov/reports/71462",
    "Larz Anderson Park": "https://www.brooklinema.gov/Facilities/Facility/Details/Larz-Anderson-Park-87",
    "Avon Hill": "https://en.wikipedia.org/wiki/Avon_Hill_Historic_District",
    "other": "Existing rendering presets; approximate landmark-area coordinates, not neighborhood boundaries",
}


def flat_indices(east, north, west, south, nx, ny):
    """Half-open cells assign every point to at most one square."""
    col = np.floor((east-west)/CELL_M).astype(int)
    row = np.floor((north-south)/CELL_M).astype(int)
    valid = (col >= 0) & (col < nx) & (row >= 0) & (row < ny)
    return row*nx+col, valid


def select_pair(cells, bounds):
    """Pick mean-temperature extrema, without consulting tree density."""
    w, s, e, n = bounds
    candidates = [r for r in cells if r["eligible"] and r["west"] >= w
                  and r["south"] >= s and r["west"]+CELL_M <= e
                  and r["south"]+CELL_M <= n]
    ordered = sorted(candidates, key=lambda r: (r["temperature"], r["id"]))
    if len(ordered) < 2:
        raise ValueError("Fewer than two qualifying squares in the landmark view")
    return {"cool": ordered[0], "hot": ordered[-1], "candidate_n": len(ordered)}


def correlations(cells):
    xs = [r["temperature"] for r in cells]
    ys = [r["tree_density"] for r in cells]
    return {"n": len(cells), "spearman": float(spearmanr(xs, ys).statistic),
            "pearson": float(pearsonr(xs, ys).statistic)}


def cell_eligible(land_fraction, heat_coverage, thermal_pixels, min_land_fraction=MIN_LAND):
    """Water-share filtering is optional; usable land heat remains required."""
    return bool(land_fraction > 0 and land_fraction >= min_land_fraction
                and heat_coverage >= MIN_HEAT and thermal_pixels >= 30)


def prepare(min_land_fraction=MIN_LAND):
    if not 0 <= min_land_fraction <= 1:
        raise ValueError("min_land_fraction must be between 0 and 1")
    base_path = OUT/"boston_canopy_heat.json"
    base = json.loads(base_path.read_text())
    frame = json.loads(Path(__file__).with_name("boston_sharing_frame.json").read_text())
    projection, metro_bounds = frame["projection"], frame["projected_bounds_km"]
    water_paths, water_meta = load_water(metro_bounds, projection)
    if water_meta["cache_sha256"] != base["provenance"]["water"]["cache_sha256"]:
        raise ValueError("Water snapshot changed")
    features = json.loads(Path(water_meta["cache"]).read_text())["features"]
    water = shapely.union_all([shapely.make_valid(shape(f["geometry"])) for f in features])
    snapshot = tree_snapshot()
    if sha(snapshot) != base["fingerprint"]["tree_sha256"]:
        raise ValueError("Tree snapshot changed")
    points, tree_meta = load_points(str(snapshot))
    source = base["provenance"]["heat"]
    stack, meta = load_heat(source["bbox"], year=source["year"])
    for key in ("crs", "transform", "dates"):
        if source[key] != meta[key]:
            raise ValueError(f"Heat {key} changed")
    dates = base["provenance"]["dates"]
    selected = stack[[meta["dates"].index(d) for d in dates]]
    mean = selected.mean(axis=0)  # A pixel must be valid on every shared date.
    affine = Affine(*meta["transform"])
    forward = Transformer.from_crs(4326, meta["crs"], always_xy=True)
    inverse = Transformer.from_crs(meta["crs"], 4326, always_xy=True)
    water_utm = transform_geometry(forward.transform, water)
    shapely.prepare(water_utm)
    wet = geometry_mask([mapping(water_utm)], mean.shape, affine, invert=True)
    mean[wet] = np.nan
    rr, cc = np.indices(mean.shape)
    east, north = affine*(cc+.5, rr+.5)
    west, south = np.floor([east.min()/CELL_M, north.min()/CELL_M])*CELL_M
    nx = int(np.floor((east.max()-west)/CELL_M))+1
    ny = int(np.floor((north.max()-south)/CELL_M))+1
    pixel_ids, _ = flat_indices(east, north, west, south, nx, ny)
    land_pixels = np.bincount(pixel_ids[~wet], minlength=nx*ny)
    valid = np.isfinite(mean)
    valid_pixels = np.bincount(pixel_ids[valid], minlength=nx*ny)
    heat_sum = np.bincount(pixel_ids[valid], weights=mean[valid], minlength=nx*ny)
    daily_sum = [np.bincount(pixel_ids[valid], weights=day[valid], minlength=nx*ny) for day in selected]
    te, tn = forward.transform(points["longitude"], points["latitude"])
    ids, in_grid = flat_indices(te, tn, west, south, nx, ny)
    on_land = np.zeros(len(te), dtype=bool)
    on_land[in_grid] = ~shapely.intersects_xy(water_utm, te[in_grid], tn[in_grid])
    tree_counts = np.bincount(ids[in_grid & on_land], minlength=nx*ny)
    x, y = project_lonlat(points["longitude"], points["latitude"], projection)
    circle = circle_fade_config(projection, .16, 1.5)
    cx, cy = circle["centre_projected_km"]
    cells = []
    for j in range(ny):
        for i in range(nx):
            idx = j*nx+i
            w, s = float(west+i*CELL_M), float(south+j*CELL_M)
            corners = np.array([[w, s], [w+CELL_M, s], [w+CELL_M, s+CELL_M], [w, s+CELL_M]])
            lon, lat = inverse.transform(corners[:, 0], corners[:, 1])
            px, py = project_lonlat(lon, lat, projection)
            if np.max(np.hypot(px-cx, py-cy)) > circle["radius_km"]:
                continue
            land_m2 = box(w, s, w+CELL_M, s+CELL_M).difference(water_utm).area
            land_share = land_m2/CELL_M**2
            coverage = valid_pixels[idx]/land_pixels[idx] if land_pixels[idx] else 0.
            temp = float(heat_sum[idx]/valid_pixels[idx]) if valid_pixels[idx] else None
            eligible = cell_eligible(land_share, coverage, valid_pixels[idx], min_land_fraction)
            cells.append({"id": f"E{int(w)}N{int(s)}", "west": w, "south": s,
                "land_km2": float(land_m2/1e6), "land_fraction": float(land_share),
                "heat_coverage": float(coverage), "thermal_grid_cells": int(valid_pixels[idx]),
                "temperature": temp, "trees": int(tree_counts[idx]),
                "tree_density": float(tree_counts[idx]/(land_m2/1e6)) if land_m2 > 0 else None,
                "daily_temperature": [float(a[idx]/valid_pixels[idx]) for a in daily_sum] if valid_pixels[idx] else [],
                "eligible": eligible})
    eligible = [r for r in cells if r["eligible"]]
    examples = []
    for place, anchor in LANDMARKS.items():
        ae, an = forward.transform(*anchor)
        bounds = [ae-VIEW_M/2, an-VIEW_M/2, ae+VIEW_M/2, an+VIEW_M/2]
        pair = select_pair(cells, bounds)
        examples.append({"place": place, "anchor_lonlat": anchor, "bounds_utm": bounds, **pair,
            "delta_temperature": pair["hot"]["temperature"]-pair["cool"]["temperature"],
            "delta_density_cool_minus_hot": pair["cool"]["tree_density"]-pair["hot"]["tree_density"]})
    examples.sort(key=lambda r: r["hot"]["temperature"], reverse=True)
    for i, r in enumerate(examples):
        r["letter"] = chr(65+i)
    result = {"schema": 1, "crs": meta["crs"], "cell_size_m": CELL_M,
        "view_size_m": VIEW_M, "grid_alignment": "UTM easting/northing multiples of 500 m; half-open cells",
        "n": len(eligible), "correlations": correlations(eligible),
        "min_land_fraction": min_land_fraction,
        "eligibility": ("Full square inside original metro circle; "
                        + (f">={min_land_fraction:.0%} land; " if min_land_fraction else "no water-share exclusion; positive land area; ")
                        + ">=90% of land raster pixels valid on ALL five dates; >=30 valid delivered pixels"),
        "temperature": "Equal-date mean on identical common valid land pixels; surface, not air temperature",
        "tree_density": "Visible published tree centers on land / exact water-excluded land area; counts use half-open grid assignment",
        "pair_selection": "Hottest and coolest qualifying complete grid cells in each fixed 2.5 km landmark view, selected by temperature alone. Ties broken by cell ID. Views can overlap and reuse cells.",
        "selection_warning": "Pairs deliberately show local heat extremes; not an independent or representative sample; correlation is not causation.",
        "landmark_sources": ANCHOR_SOURCES,
        "provenance": {"analysis_sha256": sha(base_path), "tree_sha256": sha(snapshot),
            "water": water_meta, "heat": source, "dates": dates, "tree_visibility": tree_meta,
            "heat_common_mean_sha256": hashlib.sha256(mean.tobytes()).hexdigest()},
        "cells": cells, "examples": examples}
    print(f"Grid: {len(eligible)} eligible of {len(cells)} inside-circle cells; density range "
          f"{min(r['tree_density'] for r in eligible):.0f}–{max(r['tree_density'] for r in eligible):.0f}", flush=True)
    for r in examples:
        print(r["letter"], r["place"], r["candidate_n"],
              f"{r['hot']['temperature']:.1f} / {r['cool']['temperature']:.1f} C; "
              f"{r['hot']['tree_density']:.0f} / {r['cool']['tree_density']:.0f} trees/km2", flush=True)
    return result, {"projection": projection, "metro_bounds": metro_bounds, "water_paths": water_paths,
        "circle": circle, "points": points, "x": x, "y": y, "te": te, "tn": tn,
        "tree_cell_ids": ids, "tree_on_land": on_land, "grid_west": west, "grid_south": south,
        "grid_nx": nx, "mean": mean, "affine": affine, "east": east, "north": north,
        "forward": forward, "inverse": inverse, "water_utm": water_utm, "wet": wet}
