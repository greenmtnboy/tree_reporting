#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = ["duckdb", "matplotlib", "numpy", "scipy", "shapely"]
# ///
"""Render configurable Treelines posters without requiring imagery predictions.

uv run scripts/render_city_treelines.py --city USSFO
uv run scripts/render_city_treelines.py --city USSFO --palette forest
uv run scripts/render_city_treelines.py --city USSFO --insets hayes_valley mission --dpi 600

City geometry, credits, data sources and inset locations live in
scripts/treelines_cities/<city>.json. The established Boston entry point retains
its specialized source comparisons and boundary overlays, using the same core.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patheffects as pe
from matplotlib.patches import Rectangle, ConnectionPatch
import numpy as np

from treelines_core import PALETTES, draw_map, draw_cartographic_details, draw_centered_text_runs
from treelines_data import load_city_points, load_city_water, project_lonlat


def draw_city_inset(fig, main_ax, points, x, y, projection, water_paths, palette, specification, index, scale, source_mode):
    cx, cy = project_lonlat(*specification["center_lonlat"], projection)
    size = specification["size_km"]
    bounds = (cx-size/2, cx+size/2, cy-size/2, cy+size/2)
    mask = (x >= bounds[0]) & (x < bounds[1]) & (y >= bounds[2]) & (y < bounds[3])
    count = int(mask.sum())
    if not count:
        raise ValueError(f"No selected records in {specification['label']}")
    left, width, bottom = .735, .245, .500 if index == 0 else .145
    height = width*fig.get_figwidth()/fig.get_figheight()
    ax = fig.add_axes((left, bottom, width, height))
    sources = points["data_source"][mask]
    draw_map(ax, x[mask], y[mask], sources, bounds, [(tuple(np.unique(sources)), palette["aerial"], .9)],
             .65, .10, water_paths, None, palette)
    outline = dict(fill=False, edgecolor=palette["aerial"], linewidth=.75)
    for target in (ax, main_ax):
        target.add_patch(Rectangle((bounds[0], bounds[2]), size, size, zorder=8, **outline))
    label = main_ax.text(cx, bounds[3]+.20, specification["label"].upper(), ha="center",
                        color=palette.get("map_label", palette["text"]), fontsize=7.5*scale, zorder=9)
    label.set_path_effects([pe.withStroke(linewidth=1.4*scale, foreground=palette.get("map_label_outline", palette["bg"]))])
    for source_y, target_y in ((bounds[2], 0), (bounds[3], 1)):
        fig.add_artist(ConnectionPatch(xyA=(bounds[1], source_y), coordsA=main_ax.transData,
            xyB=(0, target_y), coordsB=ax.transAxes, arrowstyle="-", lw=.6,
            color=palette["aerial"], alpha=.55))
    fig.text(left, bottom+height+.025, specification["label"].upper(), color=palette["text"], fontsize=16*scale)
    units = "inventoried trees" if source_mode == "inventory" else "mapped trees"
    fig.text(left, bottom-.019, f"{count:,} {units} · {size:g} × {size:g} km", color=palette["muted"], fontsize=8*scale)
    bx, by, distance = bounds[0]+.06*size, bounds[2]+.06*size, size/4
    ax.plot([bx,bx+distance], [by,by], color=palette["text"], lw=1.3, zorder=9)
    ax.text(bx, by+.035*size, f"{distance*1000:g} m", color=palette["text"], fontsize=7*scale, zorder=9)
    return {**specification, "projected_bounds_km": [float(v) for v in bounds], "count": count,
            "counts_by_source": {str(s): int(np.sum(sources == s)) for s in np.unique(sources)}}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--city", default="USSFO")
    parser.add_argument("--config", type=Path, help="Override the city's JSON rendering configuration")
    parser.add_argument("--input", help="Published parquet URL or local snapshot")
    parser.add_argument("--source-mode", choices=("inventory", "non-aerial", "all"), default="inventory")
    parser.add_argument("--palette", choices=tuple(PALETTES), default="light")
    parser.add_argument("--insets", nargs="*", help="Zero, one or two configured inset names; defaults to city preset")
    parser.add_argument("--width", type=float, default=18)
    parser.add_argument("--dpi", type=int, default=300)
    parser.add_argument("--dot-size", type=float, default=.22)
    parser.add_argument("--no-water", action="store_true")
    parser.add_argument("--no-circle-fade", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.width <= 0 or args.dpi <= 0 or args.dot_size <= 0:
        parser.error("Width, dpi and dot size must be positive.")
    config_path = args.config or Path(__file__).with_name("treelines_cities") / f"{args.city.lower()}.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if config["city"] != args.city.upper():
        parser.error("--city must match the selected config.")
    inset_names = config["default_insets"] if args.insets is None else args.insets
    if len(inset_names) > 2 or len(set(inset_names)) != len(inset_names) or any(n not in config["insets"] for n in inset_names):
        parser.error("Choose zero, one or two distinct configured insets: " + ", ".join(config["insets"]))
    input_location = args.input or config["input"]
    points, metadata = load_city_points(input_location, config["city"], config["inventory_sources"], args.source_mode)
    projection = {"name": "local equirectangular", "units": "km",
                  "central_longitude": config["projection_center_lonlat"][0],
                  "central_latitude": config["projection_center_lonlat"][1]}
    west, south, east, north = config["viewport_lonlat"]
    xmin, ymin = project_lonlat(west, south, projection)
    xmax, ymax = project_lonlat(east, north, projection)
    bounds = tuple(float(v) for v in (xmin, xmax, ymin, ymax))
    if not np.all(np.isfinite(bounds)) or xmin >= xmax or ymin >= ymax:
        parser.error("The configured viewport must have finite, ordered bounds.")
    for name in inset_names:
        spec = config["insets"][name]
        cx, cy = project_lonlat(*spec["center_lonlat"], projection)
        half = spec["size_km"]/2
        if half <= 0 or not (xmin <= cx-half < cx+half <= xmax and ymin <= cy-half < cy+half <= ymax):
            parser.error(f"Inset {name} must lie completely within the main viewport.")
    x, y = project_lonlat(points["longitude"], points["latitude"], projection)
    in_frame = (x >= xmin) & (x <= xmax) & (y >= ymin) & (y <= ymax)
    metadata["outside_frame_count"] = int((~in_frame).sum())
    points = {key: value[in_frame] for key, value in points.items()}
    x, y = x[in_frame], y[in_frame]
    if not len(x):
        parser.error("No selected trees in this viewport.")
    counts = {str(s): int(np.sum(points["data_source"] == s)) for s in np.unique(points["data_source"])}
    print(f"{config['city']} in frame: {len(x):,} records; sources: {counts}", flush=True)
    water_paths, water_meta = ([], []) if args.no_water else load_city_water(config, projection)
    circle = None
    if not args.no_circle_fade:
        cx, cy = project_lonlat(*config["circle_center_lonlat"], projection)
        radius = config["circle_radius_km"]
        if radius <= 0:
            parser.error("Circle radius must be positive.")
        circle = {"centre_projected_km": [float(cx),float(cy)], "radius_km": radius,
                  "fade_start_km": .87*radius, "fade_end_km": 1.03*radius,
                  "semantics": "Artistic vignette, not an imagery coverage boundary"}
    palette = PALETTES[args.palette]
    scale = args.width/18
    fig = plt.figure(figsize=(args.width,args.width*.86), facecolor=palette["bg"])
    plt.rcParams["font.family"] = "DejaVu Sans"
    fig.text(.5,.962,"\u2009".join(config["title"]),ha="center",va="top",color=palette["text"],fontsize=24*scale)
    tree_phrase = "street trees" if args.source_mode == "inventory" else "mapped trees"
    runs = [("A city revealed by its ",palette["muted"],12*scale,"normal"),
            (tree_phrase,palette.get("subtitle_trees",palette["aerial"]),12*scale,"normal")]
    if water_paths:
        runs += [(" and ",palette["muted"],12*scale,"normal"),
                 ("water",palette.get("subtitle_water","#7eaec3"),12*scale,"normal")]
    draw_centered_text_runs(fig,.5,.928,runs)
    ax = fig.add_axes((-.056362,.08783,.842724,.88434) if inset_names else (.045,.15,.91,.74))
    draw_map(ax,x,y,points["data_source"],bounds,[(tuple(counts),palette["aerial"],.8)],
             args.dot_size,.12,water_paths,circle,palette)
    inset_metadata = [draw_city_inset(fig,ax,points,x,y,projection,water_paths,palette,config["insets"][name],index,scale,args.source_mode)
                      for index,name in enumerate(inset_names)]
    draw_cartographic_details(fig,ax,bounds,palette,scale)
    count_x = .365 if inset_names else .5
    units = "inventoried trees" if args.source_mode == "inventory" else "mapped trees"
    draw_centered_text_runs(fig,count_x,.106,[(f"{len(x):,}",palette["aerial"],24*scale,"normal"),
                                            (f"  {units}",palette["aerial"],9*scale,"normal")])
    caveat = ("Street-tree inventory only; parks and private trees are not comprehensively mapped."
              if args.source_mode == "inventory" else "coverage varies by source; incomplete")
    fig.text(count_x,.061,caveat,ha="center",va="bottom",color=palette["muted"],fontsize=7*scale)
    credit = config["tree_credit"] if args.source_mode == "inventory" else config["tree_credit"] + " / © OpenStreetMap contributors (ODbL) / community"
    if water_paths:
        credit += "  |  " + config["water_credit"]
    fig.text(.475,.025,credit,ha="center",va="bottom",color=palette["muted"],fontsize=6.8*scale)
    fig.text(.98,.025,"arborary.world",ha="right",va="bottom",color=palette["muted"],fontsize=8*scale)
    output = args.output or Path(__file__).with_name(config.get("output_folder",config["slug"]+"_renders")) / f"{config['slug']}_treelines_{args.source_mode}_{args.palette}.png"
    output = output.resolve()
    output.parent.mkdir(parents=True,exist_ok=True)
    fig.savefig(output,dpi=args.dpi,facecolor=palette["bg"])
    plt.close(fig)
    metadata.update({"rendered_at_utc":datetime.now(timezone.utc).isoformat(),"config":config,
                     "config_path":str(config_path.resolve()),"projection":projection,"projected_bounds_km":bounds,
                     "in_frame_count":len(x),"in_frame_counts_by_source":counts,"insets":inset_metadata,
                     "water":water_meta,"circle_fade":circle,"palette":palette,
                     "count_semantics":"Published surviving records within the rectangular viewport, before artistic edge fade; not a census of all existing trees.",
                     "render_options":{k:str(v) if isinstance(v,Path) else v for k,v in vars(args).items()}})
    output.with_suffix(".json").write_text(json.dumps(metadata,indent=2)+"\n",encoding="utf-8")
    print(f"Saved {output}",flush=True)


if __name__ == "__main__":
    main()
