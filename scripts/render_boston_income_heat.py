#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = ["duckdb", "matplotlib", "numpy", "requests", "scipy", "shapely>=2.1", "rasterio", "pyproj"]
# ///
"""Boston tree dots with equal-area income and summer surface-heat drilldowns."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patheffects as effects
from matplotlib.patches import Rectangle, ConnectionPatch
import numpy as np
from pyproj import Transformer
import rasterio.features
from affine import Affine
import shapely
from shapely.geometry import box, shape, mapping

from boston_income_heat_data import (CACHE, HERE, PDB_URL, TIGER, load_income, load_block_groups,
                                    load_heat, poster_bbox, save_json, tree_snapshot)
from render_boston_sources_map import load_points, load_water, circle_fade_config
from treelines_core import PALETTES, draw_map, draw_cartographic_details
from treelines_data import project_lonlat

# Candidate places are fixed before examining tree density or temperature.
CANDIDATES = {
    "Nubian Square": [-71.083, 42.329], "Grove Hall": [-71.081, 42.308],
    "Mattapan Square": [-71.093, 42.269], "Codman Square": [-71.071, 42.287],
    "Fields Corner": [-71.061, 42.300], "East Boston": [-71.036, 42.376],
    "South Boston": [-71.043, 42.334], "Charlestown": [-71.061, 42.380],
    "Roslindale Village": [-71.129, 42.286], "West Roxbury Centre": [-71.159, 42.287],
    "Jamaica Plain Centre": [-71.115, 42.311], "Back Bay": [-71.082, 42.351],
    "Coolidge Corner": [-71.1213, 42.3422], "Brookline Village": [-71.119, 42.332],
    "Newton Centre": [-71.205, 42.330], "Harvard Square": [-71.119, 42.374],
    "Union Square, Somerville": [-71.095, 42.380], "Watertown Square": [-71.183, 42.365],
    "Belmont Centre": [-71.175, 42.397], "Arlington Centre": [-71.156, 42.415],
}


def transform_geometry(function, geometry):
    return shapely.transform(geometry, function, interleaved=False)


def number(value):
    try:
        result = float(str(value).replace(",", "").replace("$", ""))
        return result if result >= 0 else np.nan
    except (TypeError, ValueError):
        return np.nan


def context():
    CACHE.mkdir(parents=True, exist_ok=True)
    frame = json.loads((HERE / "boston_sharing_frame.json").read_text())
    projection, bounds = frame["projection"], frame["projected_bounds_km"]
    water_paths, water_meta = load_water(bounds, projection)
    water_features = json.loads(Path(water_meta["cache"]).read_text())["features"]
    water = shapely.union_all([shapely.make_valid(shape(f["geometry"])) for f in water_features])
    to_area = Transformer.from_crs(4326, 26986, always_xy=True).transform
    income = {r["GIDBG"]: r for r in load_income()}
    groups = []
    for feature in load_block_groups(poster_bbox()):
        p = feature["properties"]
        row = income.get(p["GEOID"])
        if row is None:
            continue
        land = shapely.make_valid(shape(feature["geometry"])).difference(water)
        if land.is_empty:
            continue
        groups.append({"id": p["GEOID"], "land": land, "area": transform_geometry(to_area, land),
                       "households": number(row["Tot_Occp_Units_ACS_20_24"]),
                       "income": number(row["avg_Agg_HH_INC_ACS_20_24"]),
                       "income_moe90": number(row["avg_Agg_HH_INC_ACSMOE_20_24"]),
                       "population": number(row["Tot_Population_ACS_20_24"]),
                       "median_income": number(row["Med_HHD_Inc_BG_ACS_20_24"]),
                       "median_income_moe90": number(row["Med_HHD_Inc_BG_ACSMOE_20_24"])})
    return projection, bounds, water, water_paths, water_meta, groups


def area_stats(label, center, projection, water, groups, side=2):
    cx, cy = project_lonlat(*center, projection)
    kx = 111.195*np.cos(np.radians(projection["central_latitude"]))
    bounds = [float(cx-side/2), float(cx+side/2), float(cy-side/2), float(cy+side/2)]
    lon0, lat0 = projection["central_longitude"], projection["central_latitude"]
    polygon = box(lon0+bounds[0]/kx, lat0+bounds[2]/111.195,
                  lon0+bounds[1]/kx, lat0+bounds[3]/111.195)
    land = polygon.difference(water)
    to_area = Transformer.from_crs(4326, 26986, always_xy=True).transform
    area = transform_geometry(to_area, land)
    total_area = transform_geometry(to_area, polygon).area
    numerator, denominator, usable_area, squared_moe, moe_households = 0., 0., 0., 0., 0.
    contributions = []
    for group in groups:
        overlap = group["area"].intersection(area).area
        if overlap <= 0:
            continue
        if group["households"] == 0:
            # A known uninhabited park/industrial block group needs no income.
            usable_area += overlap
            continue
        if not np.isfinite(group["income"]) or not group["households"] > 0:
            continue
        fraction = overlap/group["area"].area
        households = fraction*group["households"]
        numerator += households*group["income"]
        denominator += households
        usable_area += overlap
        if np.isfinite(group["income_moe90"]):
            squared_moe += (households*group["income_moe90"])**2
            moe_households += households
        contributions.append({"geoid": group["id"], "land_fraction": fraction,
                              "estimated_households": households, "mean_household_income": group["income"],
                              "mean_income_moe90": group["income_moe90"] if np.isfinite(group["income_moe90"]) else None})
    return {"label": label, "center_lonlat": center, "bounds": bounds, "polygon": polygon, "land": land,
            "land_km2": area.area/1e6, "land_share": area.area/total_area,
            "income": numerator/denominator if denominator else np.nan,
            "income_moe90_approx": np.sqrt(squared_moe)/denominator if denominator and np.isclose(moe_households, denominator) else None,
            "estimated_households": denominator, "income_land_coverage": usable_area/area.area,
            "block_groups": contributions}


def select_areas(candidates):
    eligible = [c for c in candidates if c["land_share"] >= .85 and c["income_land_coverage"] >= .9
                and c["estimated_households"] >= 500 and np.isfinite(c["income"])]
    ordered = sorted(eligible, key=lambda c: c["income"])
    if len(ordered) < 3:
        raise ValueError("Not enough populated, mostly-land candidates with income coverage")
    # Compare the lower, middle and higher ends of a predefined place list.
    # No tree or temperature measure enters this choice.
    chosen = [ordered[round(q*(len(ordered)-1))] for q in (.1, .5, .9)]
    return chosen


def sample_heat(areas, stack, meta):
    transformer = Transformer.from_crs(4326, meta["crs"], always_xy=True).transform
    masks = [rasterio.features.geometry_mask([mapping(transform_geometry(transformer, a["land"]))],
             out_shape=stack.shape[1:], transform=Affine(*meta["transform"]), invert=True) for a in areas]
    coverage = np.array([np.isfinite(stack[:, mask]).mean(axis=1) for mask in masks])
    # Dates must cover >=90% of every selected land area. All three use the
    # same dates; the mean gives each date equal weight, not each clear pixel.
    common = np.all(coverage >= .9, axis=0)
    print("Heat coverage by date:", dict(zip(meta["dates"], np.round(coverage.min(axis=0), 3).tolist())), flush=True)
    if common.sum() < 3:
        raise ValueError(f"Only {common.sum()} dates cover >=90% of all insets; inspect coverage rather than silently relaxing it")
    for area, mask, fractions in zip(areas, masks, coverage):
        means = np.array([np.nanmean(grid[mask]) for grid in stack[common]])
        area.update(temperature_c=float(means.mean()), temperature_date_sd_c=float(means.std()),
                    temperature_dates=[date for date, keep in zip(meta["dates"], common) if keep],
                    temperature_daily_means_c=means.tolist(), temperature_land_coverage=fractions.tolist())
    return common


def serializable(area):
    return {k: v for k, v in area.items() if k not in ("land", "polygon")}


def render(args, projection, bounds, water_paths, points, areas, heat_meta):
    palette = PALETTES["light"]
    green, ink, muted = palette["aerial"], palette["text"], palette["muted"]
    x, y = project_lonlat(points["longitude"], points["latitude"], projection)
    in_frame = (x>=bounds[0]) & (x<=bounds[1]) & (y>=bounds[2]) & (y<=bounds[3])
    x, y = x[in_frame], y[in_frame]
    lon, lat = points["longitude"][in_frame], points["latitude"][in_frame]
    sources = points["data_source"][in_frame]
    layers = [(tuple(np.unique(sources)), green, .85)]
    fig = plt.figure(figsize=(22, 17), facecolor=palette["bg"])
    fig.text(.5, .965, "\u2009".join("Boston, MA, USA"), ha="center", va="top", color=ink, fontsize=28)
    fig.text(.5, .928, "Trees, income & summer heat", ha="center", color=ink, fontsize=17)
    main_ax = fig.add_axes((-.035, .085, .765, .84))
    circle = circle_fade_config(projection, .16, north_shift_km=1.5)
    cx, cy = circle["centre_projected_km"]
    visible = np.hypot(x-cx, y-cy) <= circle["fade_end_km"]
    draw_map(main_ax, x[visible], y[visible], sources[visible], bounds, layers, .28, .10, water_paths, circle, palette)
    draw_cartographic_details(fig, main_ax, bounds, palette, 1)
    fig.text(.345, .087, "Every dot is a mapped tree · All sources in one color", ha="center", fontsize=12, color=ink)
    fig.text(.345, .065, "Inventories + aerial detections + community records", ha="center", fontsize=10, color=muted)
    for i, (area, tag, bottom) in enumerate(zip(areas, ("LOWER INCOME", "MIDDLE INCOME", "HIGHER INCOME"), (.66, .39, .12))):
        left, width = .73, .16
        height = width*22/17
        ab = area["bounds"]
        select = (x>=ab[0]) & (x<ab[1]) & (y>=ab[2]) & (y<ab[3])
        select[select] = shapely.intersects_xy(area["land"], lon[select], lat[select])
        fig.text(left, bottom+height+.032, f"{i+1}   {tag}", color=muted, fontsize=10)
        fig.text(left, bottom+height+.010, area["label"], color=ink, fontsize=16)
        inset = fig.add_axes((left, bottom, width, height))
        draw_map(inset, x[select], y[select], sources[select], ab, layers, .60, 0, water_paths, palette=palette)
        outline = dict(fill=False, edgecolor=palette["records"], linewidth=.8, zorder=8)
        for ax in (main_ax, inset):
            ax.add_patch(Rectangle((ab[0], ab[2]), ab[1]-ab[0], ab[3]-ab[2], **outline))
        main_ax.text((ab[0]+ab[1])/2, ab[3]+.22, str(i+1), ha="center", color=ink, fontsize=16,
                     path_effects=[effects.withStroke(linewidth=3, foreground=palette["bg"])], zorder=9)
        for sy, ty in ((ab[2], 0), (ab[3], 1)):
            fig.add_artist(ConnectionPatch(xyA=(ab[1], sy), coordsA=main_ax.transData,
                           xyB=(0, ty), coordsB=inset.transAxes, color=palette["records"],
                           linewidth=.55, alpha=.30, zorder=6))
        inset.plot([ab[0]+.12, ab[0]+.62], [ab[2]+.12]*2, color=ink, linewidth=1.2, zorder=8)
        inset.text(ab[0]+.12, ab[2]+.19, "500 m", fontsize=7, color=ink, zorder=8)
        stats_x = .905
        stats = [(f"${area['income']/1000:.0f}k", "mean household income", ink),
                 (f"{area['tree_density_km2']:,.0f}", "mapped trees / km² land", green),
                 (f"{area['temperature_c']:.1f}°C", f"{area['temperature_c']*1.8+32:.1f}°F · surface", "#96563D")]
        for j, (value, label, color) in enumerate(stats):
            yy = bottom+height-.034-j*.071
            fig.text(stats_x, yy, value, fontsize=22, color=color)
            fig.text(stats_x, yy-.019, label, fontsize=8.3, color=muted)
        fig.text(left, bottom-.018, f"2 × 2 km view · {area['tree_count']:,} mapped trees · {area['land_km2']:.2f} km² land",
                 fontsize=8.4, color=muted)
    n = len(areas[0]["temperature_dates"])
    fig.text(.5, .040, f"Income: ACS 2020–2024 (2024 dollars), estimated for each square. Heat: mean of {n} shared clear-sky summer {heat_meta['year']} dates; surface, not air temperature.",
             fontsize=9, color=muted, ha="center")
    fig.text(.5, .025, "Mapped tree density is not canopy cover. Coverage varies by source; trees are missed. Three examples describe local differences, not a causal relationship.",
             fontsize=8.5, color=muted, ha="center")
    fig.text(.47, .010, "Trees: local inventories / © OpenStreetMap contributors / USDA National Agriculture Imagery Program   ·   Income: U.S. Census   ·   Heat: USGS Landsat 8/9   ·   Water: MassGIS",
             fontsize=8, color=muted, ha="center")
    fig.text(.985, .010, "arborary.world", fontsize=9, color=ink, ha="right")
    args.output.parent.mkdir(exist_ok=True, parents=True)
    fig.savefig(args.output, dpi=args.dpi, facecolor=palette["bg"])
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--year", type=int, default=2025)
    parser.add_argument("--dpi", type=int, default=220)
    parser.add_argument("--inspect-income", action="store_true")
    parser.add_argument("--insets", nargs=3, choices=tuple(CANDIDATES), help="Override the three named places, in increasing income order")
    parser.add_argument("--output", type=Path, default=HERE / "boston_renders" / "boston_trees_income_summer_heat.png")
    args = parser.parse_args()
    if args.dpi <= 0:
        parser.error("--dpi must be positive")
    projection, bounds, water, water_paths, water_meta, groups = context()
    candidates = [area_stats(label, center, projection, water, groups) for label, center in CANDIDATES.items()]
    areas = ([next(a for a in candidates if a["label"] == name) for name in args.insets]
             if args.insets else select_areas(candidates))
    if len({a["label"] for a in areas}) != 3 or any(areas[i]["income"] >= areas[i+1]["income"] for i in range(2)):
        parser.error("Choose three different places in increasing income order")
    for a in sorted(candidates, key=lambda a:a["income"]):
        print(f"{a['label']:28s} ${a['income']:9,.0f}  land={a['land_share']:.0%} income coverage={a['income_land_coverage']:.0%}", flush=True)
    print("Selected by income:", [a["label"] for a in areas], flush=True)
    save_json(CACHE / "income-candidates.json", [serializable(a) for a in candidates])
    if args.inspect_income:
        return
    stack, heat_meta = load_heat(poster_bbox(), args.year)
    sample_heat(areas, stack, heat_meta)
    path = args.input or tree_snapshot()
    points, tree_meta = load_points(str(path))
    x, y = project_lonlat(points["longitude"], points["latitude"], projection)
    for a in areas:
        ab = a["bounds"]
        mask = (x>=ab[0]) & (x<ab[1]) & (y>=ab[2]) & (y<ab[3])
        land_mask = mask.copy()
        land_mask[mask] = shapely.intersects_xy(a["land"], points["longitude"][mask], points["latitude"][mask])
        a["tree_count"] = int(land_mask.sum())
        a["mapped_records_over_water_excluded"] = int(mask.sum()-land_mask.sum())
        a["tree_density_km2"] = a["tree_count"]/a["land_km2"]
        a["tree_sources"] = dict(Counter(points["data_source"][land_mask].tolist()))
        print(a["label"], a["tree_count"], round(a["tree_density_km2"]), round(a["temperature_c"], 2), flush=True)
    render(args, projection, bounds, water_paths, points, areas, heat_meta)
    metadata = {"rendered_utc": datetime.now(timezone.utc).isoformat(), "projection": projection,
                "bounds": bounds, "areas": [serializable(a) for a in areas], "temperature": heat_meta,
                "trees": tree_meta, "tree_input": str(path), "tree_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "income_source": PDB_URL, "boundaries_source": TIGER, "water": water_meta,
                "income_method": "Block-group mean household incomes weighted by estimated households in each square; households apportioned by intersecting land area. Assumes households evenly distributed within block-group land. Means are ACS 2020–2024 in 2024 dollars.",
                "income_uncertainty": "Input block-group 90% MOEs retained. Approximate weighted-mean MOE treats weights as fixed; spatial interpolation adds unquantified error.",
                "selection": ("Explicit --insets override in increasing income order." if args.insets else
                              "10th, 50th and 90th percentile rank of mean income in the predefined named-place candidate list; >=85% land, >=90% income area coverage, >=500 estimated households. Selection precedes trees and heat."),
                "input_hashes": {"income": hashlib.sha256((CACHE/"pdb2026-massachusetts.json").read_bytes()).hexdigest()},
                "tree_snapshot_source": (json.loads(path.with_suffix(".source.json").read_text())
                                         if path.with_suffix(".source.json").exists() else None),
                "tree_density": "Surviving visible published records from all sources divided by land area (MassGIS open water excluded). Not canopy cover or a completeness estimate."}
    save_json(args.output.with_suffix(".json"), metadata)
    print(f"Saved {args.output}", flush=True)


if __name__ == "__main__":
    main()
