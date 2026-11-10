#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = ["duckdb", "matplotlib", "numpy", "requests", "scipy", "shapely>=2.1"]
# ///
"""Render SF tree locations by park, street right-of-way, and likely private parcel."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
import numpy as np
import shapely

from sf_tree_settings import CPAD, ROLL_YEAR, classify, fetch_inputs, geometry, park_features, rows
from treelines_core import PALETTES, draw_cartographic_details, draw_centered_text_runs, draw_map
from treelines_data import load_city_points, load_city_water, project_lonlat

HERE = Path(__file__).resolve().parent
COLORS = {"unclassified": "#767B79", "park": "#216548", "street": "#8B651E", "private": "#775580"}
METHODOLOGY = {
    "scope": "Surviving published tree coordinates on SF shoreline/islands land within the rectangular poster frame, before fading.",
    "precedence": ["public CPAD park or protected open space", "SF Public Works 2014 right of way",
                   "likely private parcel", "other or unresolved, shown in gray"],
    "parks": "CPAD 2024a city, county, special district, state and federal park units. Smaller unit wins overlaps.",
    "private_proxy": "Active parcel with positive assessed land value in a residential, commercial or industrial assessor class. City Lands, government and possessory-interest matches are excluded. Ownership is inferred.",
    "private_uses": "Golf courses use assessor class GC. Residential, golf and other business counts use single-use matches. Locations with multiple qualifying uses are reported as mixed.",
    "streets": "Right-of-way match outside park polygons. Official means surviving source SF_OPENDATA. Percentages use all mapped street trees.",
}


def sidebar(fig, counts, parks, street_sources, parcels, official):
    palette = PALETTES["light"]
    left, number, right = .73, .894, .963

    def label(x, y, value, size=11, color=None, **style):
        fig.text(x, y, value, va="baseline", fontsize=size, color=color or palette["text"], **style)

    def heading(y, title, key, description):
        ink = COLORS[key]
        label(left, y, "\u2009".join(title), 15, ink)
        label(right, y, f"{counts[key]:,}", 19, ink, ha="right")
        fig.add_artist(Line2D([left, right], [y-.012, y-.012], transform=fig.transFigure,
                              color=ink, alpha=.48, linewidth=.65))
        label(left, y-.035, description, 10, palette["muted"])

    def row(y, title, count, total):
        label(left, y, title, 10.8)
        label(number, y, f"{count:,}", 11, ha="right")
        label(right, y, f"{100*count/total:.1f}%", 10.8, palette["muted"], ha="right")

    heading(.811, "Park trees", "park", "Public parks and protected open space")
    top = parks.most_common(3)
    names = {"Golden Gate National Recreation Area": "Golden Gate NRA"}
    for i, (name, count) in enumerate(top):
        row(.735-i*.028, names.get(name, name), count, counts["park"])
    row(.651, "Other park units", counts['park']-sum(n for _,n in top), counts["park"])

    heading(.586, "Street trees", "street", "Within mapped street rights-of-way")
    official_count = sum(street_sources.get(s, 0) for s in official)
    row(.51, "City inventory", official_count, counts["street"])
    row(.482, "Aerial and community", counts["street"]-official_count, counts["street"])

    heading(.417, "Likely private trees", "private", "On parcels matched to the assessor roll")
    private_rows = [("residential", "Residential"), ("commercial or industrial", "Business / industrial"),
                    ("golf courses", "Golf courses"), ("mixed", "Mixed use")]
    for i, (key, title) in enumerate(private_rows):
        n = parcels[key]
        row(.341-i*.028, title, n, counts["private"])
    heading(.192, "Unclassified trees", "unclassified", "Other public land or unresolved parcel matches")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", help="Published tree parquet URL or local snapshot")
    parser.add_argument("--dpi", type=int, default=300)
    parser.add_argument("--output", type=Path, default=HERE/"sf_renders"/"trees_of_san_francisco_by_land_setting.png")
    args = parser.parse_args()
    if args.dpi <= 0:
        parser.error("--dpi must be positive")
    config = json.loads((HERE/"treelines_cities"/"ussfo.json").read_text())
    layers = fetch_inputs()
    parks = park_features()
    points, metadata = load_city_points(args.input or config["input"], config["city"], config["inventory_sources"], "all")
    lon, lat = points["longitude"], points["latitude"]
    west, south, east, north = config["viewport_lonlat"]
    land = shapely.union_all([geometry(r["the_geom"]) for r in rows(layers["land"][0])])
    inside = (lon>=west) & (lon<=east) & (lat>=south) & (lat<=north) & shapely.intersects_xy(land, lon, lat)
    lon, lat, sources = lon[inside], lat[inside], points["data_source"][inside]
    setting, park_counts, private_counts, overlap = classify(shapely.points(lon, lat), layers, parks)
    counts = Counter(setting.tolist())
    by_source = {key: dict(Counter(sources[setting==key].tolist())) for key in counts}
    shown = sum(counts[key] for key in COLORS)
    assert sum(park_counts.values()) == counts["park"] and sum(private_counts.values()) == counts["private"]
    assert all(counts[key] for key in ("park", "street", "private")) and shown == len(lon)
    print(dict(counts), flush=True)
    print(park_counts.most_common(5), by_source["street"], private_counts, flush=True)

    projection = dict(zip(("central_longitude", "central_latitude"), config["projection_center_lonlat"]))
    x, y = project_lonlat(lon, lat, projection)
    xmin, ymin = project_lonlat(west, south, projection)
    xmax, ymax = project_lonlat(east, north, projection)
    bounds = xmin, xmax, ymin, ymax
    water, water_provenance = load_city_water(config, projection)
    cx, cy = project_lonlat(*config["circle_center_lonlat"], projection)
    radius = config["circle_radius_km"]
    circle = {"centre_projected_km": [cx, cy], "fade_start_km": .87*radius, "fade_end_km": 1.03*radius}
    palette = PALETTES["light"]
    fig = plt.figure(figsize=(18,15.48), facecolor=palette["bg"])
    fig.text(.5,.962,"\u2009".join(config["title"]),ha="center",va="top",color=palette["text"],fontsize=24)
    draw_centered_text_runs(fig,.5,.928,[
        ("Mapped trees in ",palette["muted"],12,"normal"), ("parks",COLORS["park"],12,"normal"),
        (", along ",palette["muted"],12,"normal"), ("streets",COLORS["street"],12,"normal"),
        (", on ",palette["muted"],12,"normal"), ("likely private land",COLORS["private"],12,"normal"),
        (" and in ",palette["muted"],12,"normal"), ("unclassified areas",COLORS["unclassified"],12,"normal")])
    ax = fig.add_axes((-.056362,.08783,.842724,.88434))
    draw_map(ax,x,y,setting,bounds,[((k,),c,.80) for k,c in COLORS.items()],.25,.15,water,circle,palette)
    draw_cartographic_details(fig,ax,bounds,palette,1.)
    sidebar(fig,counts,park_counts,by_source["street"],private_counts,config["inventory_sources"])
    draw_centered_text_runs(fig,.365,.109,[(f"{shown:,}",palette["text"],26,"normal"),
                                          ("  mapped trees",palette["text"],11,"normal")])
    fig.text(.5,.053,"Tree locations determine the category. Parks take priority at overlaps. Private land is inferred from parcel and tax records.",
             ha="center",fontsize=8.4,color=palette["muted"])
    fig.text(.47,.029,"Trees: SF Public Works / © OpenStreetMap contributors / USDA National Agriculture Imagery Program     |     Land: CPAD 2024a / DataSF rights-of-way (2014), parcels, City Lands and 2025 assessor roll",
             ha="center",fontsize=7.1,color=palette["muted"])
    fig.text(.47,.014,"Shoreline and water: DataSF / California Department of Technology / NOAA CUSP",
             ha="center",fontsize=7,color=palette["muted"])
    fig.text(.98,.018,"arborary.world",ha="right",fontsize=9,color=palette["text"])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.output, dpi=args.dpi, facecolor=palette["bg"])
    plt.close(fig)
    metadata.update({"rendered_utc": datetime.now(timezone.utc).isoformat(), "methodology": METHODOLOGY,
                     "palette": COLORS, "viewport_lonlat": config["viewport_lonlat"], "trees_on_sf_land_in_frame": len(lon),
                     "shown": shown, "counts": dict(counts), "counts_by_tree_source": by_source,
                     "parks": dict(park_counts.most_common()), "private_parcel_uses": private_counts,
                     "park_street_overlap_assigned_to_park": overlap, "assessor_roll_year": ROLL_YEAR,
                     "land_sources": {k:v[1] for k,v in layers.items()}, "parks_source": CPAD, "water": water_provenance})
    args.output.with_suffix(".json").write_text(json.dumps(metadata, indent=2)+"\n",encoding="utf-8")
    print(f"Saved {args.output}", flush=True)


if __name__ == "__main__":
    main()
