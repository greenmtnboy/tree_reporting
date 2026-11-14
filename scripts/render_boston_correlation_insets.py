#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = ["duckdb", "matplotlib", "numpy", "requests", "scipy", "statsmodels", "shapely>=2.1", "rasterio", "pyproj"]
# ///
"""Two association charts and twelve tree/heat maps with compact metric bars."""
import argparse
import hashlib
import io
import json
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.patheffects as effects
from matplotlib.collections import LineCollection
from matplotlib.lines import Line2D
from matplotlib.colors import LinearSegmentedColormap, Normalize, to_rgb
from matplotlib.font_manager import FontProperties
from matplotlib.offsetbox import AnnotationBbox, DrawingArea
from matplotlib.patches import Circle, PathPatch
from matplotlib.textpath import TextPath
from matplotlib.ticker import FuncFormatter
from matplotlib.transforms import Affine2D, Bbox
import numpy as np
from affine import Affine
from pyproj import Transformer
import rasterio.features
from shapely.geometry import Point
import shapely
from statsmodels.nonparametric.smoothers_lowess import lowess

from boston_income_heat_data import HERE, tree_snapshot, save_json, load_heat
from render_boston_income_heat import CANDIDATES, context, transform_geometry
from render_boston_sources_map import load_points, circle_fade_config
from treelines_core import PALETTES, draw_map
from treelines_data import project_lonlat

PLACES = ("Nubian Square", "Egleston Square", "Union Square, Somerville",
          "Brookline Village", "Avon Hill", "Newmarket",
          "Mattapan Square", "Roslindale Village", "Larz Anderson Park",
          "Corey Hill Park", "Watertown Square", "Edgeworth, Malden")
LANDMARKS = {**CANDIDATES, "Allston Village": [-71.1325, 42.3535]}
# Deliberate range examples, not a representative neighborhood sample. Keep
# exact analyzed geographies so the poster cannot silently choose another group.
RANGE_EXAMPLES = {
    "Egleston Square": ("250250813011", "Lowest median household income"),
    "Avon Hill": ("250173545002", "Highest mapped tree density"),
    "Newmarket": ("250250801001", "Lowest mapped tree density"),
    "Larz Anderson Park": ("250214011001", "Lowest mean summer surface temperature"),
    "Corey Hill Park": ("250214004022", "Upper income interval ($250k+), tied with other groups"),
    "Edgeworth, Malden": ("250173412002", "Highest mean summer surface temperature"),
}
COLORS = ("#217A70", "#9A6B1E", "#B5553C", "#805A8D", "#487FA1", "#6B7730",
          "#A44C73", "#52676D", "#3B6C37", "#A46343", "#60639A", "#77613B")
FIG_WIDTH, FIG_HEIGHT = 22, 25
SMOOTH_FRACTION = .6
HEAT_LIMITS = (30, 44)


def prepare_heat_tiles(examples, provenance, projection, water):
    """Use real grid-cell corners; retain only pixels valid on all shared dates."""
    source = provenance["temperature"]
    stack, meta = load_heat(source["bbox"], year=source["year"])
    for key in ("crs", "transform", "dates"):
        if meta[key] != source[key]:
            raise ValueError(f"Heat {key} differs from the analysis")
    dates = provenance["dates"]
    selected = stack[[meta["dates"].index(date) for date in dates]]
    # np.mean deliberately preserves NaN where even one date is missing.
    mean = selected.mean(axis=0)
    transform = Affine(*meta["transform"])
    to_lonlat = Transformer.from_crs(meta["crs"], 4326, always_xy=True)
    to_utm = Transformer.from_crs(4326, meta["crs"], always_xy=True)
    rr, cc = np.indices(mean.shape)
    east, north = transform * (cc+.5, rr+.5)
    lon, lat = to_lonlat.transform(east, north)
    px, py = project_lonlat(lon, lat, projection)
    for row in examples:
        zone = rasterio.features.geometry_mask(
            [shapely.geometry.mapping(transform_geometry(to_utm.transform, row["land"]))],
            out_shape=mean.shape, transform=transform, invert=True)
        daily = np.nanmean(selected[:, zone], axis=1)
        if not np.allclose(daily, row["daily_temperature"], atol=1e-4, rtol=0):
            raise ValueError(f"{row['id']}: heat raster differs from analyzed temperatures")
        b = row["bounds"]
        inside = (px>=b[0]) & (px<=b[1]) & (py>=b[2]) & (py<=b[3])
        rows, cols = np.where(inside)
        r0, r1 = max(0, rows.min()-1), min(mean.shape[0], rows.max()+2)
        c0, c1 = max(0, cols.min()-1), min(mean.shape[1], cols.max()+2)
        tile = mean[r0:r1, c0:c1].copy()
        wet = shapely.intersects_xy(water, lon[r0:r1, c0:c1], lat[r0:r1, c0:c1])
        tile[wet] = np.nan
        er, ec = np.meshgrid(np.arange(r0, r1+1), np.arange(c0, c1+1), indexing="ij")
        elon, elat = to_lonlat.transform(*(transform * (ec, er)))
        gx, gy = project_lonlat(elon, elat, projection)
        row["heat_grid"] = (gx, gy, tile)
        local = inside[r0:r1, c0:c1] & ~wet
        row["heat_shading"] = {
            "valid_land_fraction": float(np.isfinite(tile[local]).mean()),
            "range_c": [float(np.nanmin(tile)), float(np.nanmax(tile))]}
        print("Heat", row["place"], row["heat_shading"], flush=True)
    return {"dates": dates, "grid_m": meta["grid_m"],
            "native_thermal_sampling_m": meta["native_thermal_sampling_m"],
            "quality": meta["quality"], "color_limits_c": HEAT_LIMITS,
            "aggregation": "Equal-date mean; every pixel must be valid on all five dates",
            "display": "Projected original grid-cell corners; no spatial smoothing; water masked"}


def letter_marker(ax, x, y, letter, color, background, diameter=12, fontsize=7.5, style="outline"):
    """Center the visible letter outline, not the font's ascent/descent box."""
    area = DrawingArea(diameter, diameter, 0, 0)
    center = diameter / 2
    dark = tuple(.75*np.array(to_rgb(color))+.25*np.array(to_rgb("#233b30")))
    if style != "label":
        fill = (tuple(.12*np.array(to_rgb(color))+.88*np.array(to_rgb(background)))
                if style == "soft" else background)
        # A fine keyline and opaque paper-colored fill remain clear over trees,
        # without the bright white rim and solid badge of the original version.
        area.add_artist(Circle((center, center), center-.6, facecolor=fill,
                               edgecolor=dark, linewidth=.65 if style == "outline" else .4))
    glyph = TextPath((0, 0), letter, size=fontsize,
                     prop=FontProperties(family="DejaVu Sans", weight="normal"))
    bounds = glyph.get_extents()
    centered = Affine2D().translate(center-(bounds.x0+bounds.x1)/2,
                                    center-(bounds.y0+bounds.y1)/2).transform_path(glyph)
    path = PathPatch(centered, facecolor=dark, edgecolor="none")
    if style == "label":
        path.set_path_effects([effects.withStroke(linewidth=2, foreground=background)])
    area.add_artist(path)
    marker = AnnotationBbox(area, (x, y), frameon=False, pad=0,
                           box_alignment=(.5, .5), zorder=8)
    ax.add_artist(marker)
    return marker


def choose_examples(rows, groups, projection):
    """Combine six explicit range examples with six geographic examples."""
    geometry = {g["id"]: g["land"] for g in groups}
    available = [r for r in rows if r["eligible"]]
    selected, used = [], set()
    for place in PLACES:
        if place in RANGE_EXAMPLES:
            geoid, reason = RANGE_EXAMPLES[place]
            row = next((r for r in available if r["id"] == geoid), None)
            if row is None or geoid in used:
                raise ValueError(f"Range example {place} is missing, ineligible, or duplicated")
            selected.append({**row, "place": place, "land": geometry[geoid], "selection_reason": reason})
            used.add(geoid)
            continue
        point = Point(*LANDMARKS[place])
        px, py = project_lonlat(point.x, point.y, projection)
        candidates = [r for r in available if r["id"] not in used]
        # Prefer the block group containing the landmark; otherwise the nearest
        # eligible centroid. Labels explicitly say "near" rather than claiming
        # the entire neighborhood shares this block group's measurements.
        row = min(candidates, key=lambda r: (not geometry[r["id"]].covers(point),
                                             np.hypot(r["x_km"]-px, r["y_km"]-py)))
        distance = float(np.hypot(row["x_km"]-px, row["y_km"]-py))
        if distance > 1:
            raise ValueError(f"No eligible block group within 1 km of {place}")
        selected.append({**row, "place": place, "land": geometry[row["id"]], "landmark_distance_km": distance,
                         "selection_reason": "Geographic context example near a fixed landmark"})
        used.add(row["id"])
    for metric in ("income", "tree_density", "temperature"):
        if (min(r[metric] for r in selected) != min(r[metric] for r in available)
                or max(r[metric] for r in selected) != max(r[metric] for r in available)):
            raise ValueError(f"Examples no longer span the matched sample's {metric} range; review range choices")
    selected.sort(key=lambda r: r["temperature"], reverse=True)
    for i, row in enumerate(selected):
        row.update(letter=chr(65+i), color=COLORS[i])
    return selected


def boundary_lines(geometry):
    polygons = [geometry] if geometry.geom_type == "Polygon" else geometry.geoms
    return [np.asarray(r.coords) for p in polygons if p.geom_type == "Polygon"
            for r in (p.exterior, *p.interiors)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dpi", type=int, default=220)
    parser.add_argument("--output", type=Path, default=HERE/"boston_renders"/"boston_correlations_with_tree_insets.png")
    parser.add_argument("--no-heat-shading", action="store_true", help="Keep the temperature insets as tree-only maps")
    parser.add_argument("--marker-style", choices=("outline", "soft", "label"), default="outline")
    parser.add_argument("--marker-review", action="store_true", help="Also export a comparison of all three marker styles")
    parser.add_argument("--chart-layout", choices=("leaders", "badges"), default="leaders",
                        help="Shared location list with leaders, or the earlier letter-marker layout")
    args = parser.parse_args()
    if args.dpi <= 0:
        parser.error("--dpi must be positive")
    if args.marker_review and args.chart_layout != "badges":
        parser.error("--marker-review requires --chart-layout badges")
    result_path = HERE/"boston_renders"/"boston_income_tree_heat_correlations.json"
    result = json.loads(result_path.read_text())
    if result["provenance"].get("income", {}).get("statistic") != "median household income":
        raise ValueError("Rerun analyze_boston_income_heat.py to generate median-income analysis first")
    rows = json.loads(result_path.with_name(result_path.stem+"_block_groups.json").read_text())
    projection, metro_bounds, water, water_paths, water_meta, groups = context()
    examples = choose_examples(rows, groups, projection)
    path = tree_snapshot()
    if hashlib.sha256(path.read_bytes()).hexdigest() != result["provenance"]["tree_sha256"]:
        raise ValueError("Tree snapshot differs from the analysis; rerun the analysis first")
    if water_meta["cache_sha256"] != result["provenance"]["water"]["cache_sha256"]:
        raise ValueError("Water geometry differs from the analysis; rerun the analysis first")
    points, _ = load_points(str(path))
    x, y = project_lonlat(points["longitude"], points["latitude"], projection)
    project = lambda lon, lat: project_lonlat(lon, lat, projection)
    for row in examples:
        row["local_land"] = transform_geometry(project, row["land"])
    span = max(max(r["local_land"].bounds[2]-r["local_land"].bounds[0],
                   r["local_land"].bounds[3]-r["local_land"].bounds[1]) for r in examples)
    side = float(np.ceil((span+.12)*2)/2)
    for row in examples:
        xmin, ymin, xmax, ymax = row["local_land"].bounds
        cx, cy = (xmin+xmax)/2, (ymin+ymax)/2
        row["bounds"] = [cx-side/2, cx+side/2, cy-side/2, cy+side/2]
        b = row["bounds"]
        mask = (x>=b[0]) & (x<b[1]) & (y>=b[2]) & (y<b[3])
        inside = shapely.intersects_xy(row["land"], points["longitude"][mask], points["latitude"][mask])
        row["plot_x"], row["plot_y"] = x[mask], y[mask]
        row["plot_group"] = np.where(inside, "selected", "context")
        if int(inside.sum()) != row["trees"]:
            # The analysis allocates shared-boundary points to one block group.
            # Do not silently render a conflicting numerator.
            raise ValueError(f"{row['id']}: inset tree count differs from analysis ({inside.sum()} vs {row['trees']})")
        print(row["letter"], row["place"], row["id"], round(row["income"]),
              round(row["tree_density"]), round(row["temperature"], 1), flush=True)

    palette = PALETTES["light"]
    heat_meta = None if args.no_heat_shading else prepare_heat_tiles(examples, result["provenance"], projection, water)
    # Preblend colors so the shared legend exactly matches the subtle map wash.
    heat_colors = [tuple(.34*np.array(to_rgb(c))+.66*np.array(to_rgb(palette["bg"])))
                   for c in ("#3E8098", "#D9D4B5", "#B94E32")]
    heat_cmap = LinearSegmentedColormap.from_list("subtle_surface_heat", heat_colors)
    heat_norm = Normalize(*HEAT_LIMITS, clip=True)
    ink, muted = palette["text"], palette["muted"]
    fig = plt.figure(figsize=(FIG_WIDTH, FIG_HEIGHT), facecolor=palette["bg"])
    marker_specs, marker_artists = [], []

    def add_marker(ax, row, mx, my, diameter, fontsize):
        spec = (ax, mx, my, row["letter"], row["color"], palette["bg"], diameter, fontsize)
        marker_specs.append(spec)
        marker_artists.append(letter_marker(*spec, style=args.marker_style))
    # Keep the established metro footprint; this locator adds geographic context,
    # not a third statistical panel or a different analysis sample.
    leaders = args.chart_layout == "leaders"
    if leaders:
        # Extend the export upward: existing chart and inset heights stay intact.
        header_text = []
        header_text.append(fig.text(.265, 1.067, "\u2009".join("Boston, MA, USA"), fontsize=29, color=ink, va="top"))
        header_text.append(fig.text(.265, 1.041, "Trees, income & summer heat", fontsize=25, color=ink, va="top"))
        header_text.append(fig.text(.265, 1.013, f"{result['n']} Census block groups · Twelve views spanning the observed range", fontsize=12, color=ink))
        header_text.append(fig.text(.265, .996, "All mapped tree sources · Median household income · Summer surface temperature",
                 fontsize=11, color=muted))
        header_text.append(fig.text(.265, .979, "Five clear-sky dates, summer 2025 · Correlation, not causation", fontsize=11, color=muted))
        # Center the map + text as a single measured group, rather than centering
        # just the title or assuming that the longest line fills the page.
        header_right = max(t.get_window_extent(fig.canvas.get_renderer()).transformed(
            fig.transFigure.inverted()).x1 for t in header_text)
        header_shift = .5-(.08+header_right)/2
        for text in header_text:
            text.set_x(text.get_position()[0]+header_shift)
    else:
        fig.text(.5, .963, "\u2009".join("Boston, MA, USA · Trees, income & summer heat"), ha="center", va="top", fontsize=25, color=ink)
    metro_ax = fig.add_axes((.08+header_shift, .958, .14, .14*FIG_WIDTH/FIG_HEIGHT) if leaders
                           else (.407, .751, .186, .186*FIG_WIDTH/FIG_HEIGHT))
    circle = circle_fade_config(projection, .16, north_shift_km=1.5)
    cx, cy = circle["centre_projected_km"]
    visible = np.hypot(x-cx, y-cy) <= circle["fade_end_km"]
    sources = points["data_source"][visible]
    draw_map(metro_ax, x[visible], y[visible], sources, metro_bounds,
             [(tuple(np.unique(sources)), palette["aerial"], .18 if leaders else .65)], .035, 0,
             water_paths, circle, palette)
    for row in examples:
        if leaders:
            metro_ax.scatter(row["x_km"], row["y_km"], s=20, c=row["color"],
                             edgecolors=palette["bg"], linewidths=.5, zorder=8)
        else:
            add_marker(metro_ax, row, row["x_km"], row["y_km"], diameter=8.5, fontsize=5.5)
    if not leaders:
        fig.text(.5, .928, "BOSTON METRO", ha="center", fontsize=10, color=ink)
        fig.text(.5, .742, "All mapped trees · Twelve locations", ha="center", fontsize=8, color=muted)
    # Both charts share tree density on Y. Matching the list's vertical order
    # to density keeps the leader lines from crossing in the central gutters.
    label_rows = sorted(examples, key=lambda r: r["tree_density"], reverse=True)
    label_y = {r["id"]: yy for r, yy in zip(label_rows, np.linspace(.893, .767, len(examples)))}
    label_left, label_right = {}, {}
    if leaders:
        list_center = (.39+.65)/2
        fig.text(list_center, .928, "LOCATIONS", ha="center", fontsize=10, color=ink)
        for row in label_rows:
            yy = label_y[row["id"]]
            letter = fig.text(0, yy, row["letter"], color=row["color"], fontsize=9, va="center")
            name = fig.text(.014, yy, row["place"].replace(", Somerville", ""), color=ink,
                            fontsize=9, va="center")
            bounds = Bbox.union([t.get_window_extent(fig.canvas.get_renderer()).transformed(
                fig.transFigure.inverted()) for t in (letter, name)])
            shift = list_center-(bounds.x0+bounds.x1)/2
            for text in (letter, name):
                text.set_x(text.get_position()[0]+shift)
            label_left[row["id"]] = bounds.x0+shift-.006
            label_right[row["id"]] = bounds.x1+shift+.006
    sample = [r for r in rows if r["eligible"]]
    density = np.array([r["tree_density"] for r in sample])
    ymax = float(np.ceil(density.max()/1000)*1000)
    for column, (key, title, xlabel, pair) in enumerate((
            ("income", "Income & trees", "Median household income · 2024 dollars", "income_vs_tree_density"),
            ("temperature", "Summer heat & trees", "Summer surface temperature · °C", "tree_density_vs_temperature"))):
        chart_left = (.08 if column == 0 else .65) if leaders else (.065 if column == 0 else .635)
        chart_width = .31 if leaders else .32
        ax = fig.add_axes((chart_left, .745, chart_width, .16), facecolor=palette["bg"])
        xx = np.array([r[key] for r in sample])
        capped = np.array([r["income_bound"] is not None for r in sample]) if key == "income" else np.zeros(len(sample), dtype=bool)
        ax.scatter(xx[~capped], density[~capped], s=16, color="#9CA99E", alpha=.55, edgecolors="none", zorder=2)
        for bound, marker in (("upper", ">"), ("lower", "<")):
            selected = np.array([r["income_bound"] == bound for r in sample]) if key == "income" else np.zeros(len(sample), dtype=bool)
            ax.scatter(xx[selected], density[selected], s=20, marker=marker, color="#9CA99E", alpha=.65, edgecolors="none", zorder=2)
        # Robust local linear smoothing uses all observations. Draw only the
        # central 95% of X to avoid visually overinterpreting sparse tails.
        # Censored medians have no exact X coordinate: show at the published
        # limit, but exclude from the income curve rather than invent a value.
        fit_x, fit_y = xx[~capped], density[~capped]
        curve_x = np.linspace(*np.quantile(fit_x, [.025, .975]), 240)
        curve_y = lowess(fit_y, fit_x, frac=SMOOTH_FRACTION, it=3, xvals=curve_x)
        ax.plot(curve_x, curve_y,
                color=muted, lw=1.2, alpha=.65, zorder=1)
        for row in examples:
            if leaders:
                point_marker = ({"upper": ">", "lower": "<"}.get(row["income_bound"], "o") if key == "income" else "o")
                ax.scatter(row[key], row["tree_density"], s=45, c=row["color"], marker=point_marker,
                           edgecolors=palette["bg"], linewidths=.65, zorder=8)
            else:
                add_marker(ax, row, row[key], row["tree_density"], diameter=12, fontsize=7.5)
        ax.set_ylim(0, ymax*1.04)
        ax.set_xlabel(xlabel, fontsize=10 if leaders else 12, color=ink, labelpad=9)
        ax.set_ylabel("Mapped trees / km² land", fontsize=11 if leaders else 12, color=ink, labelpad=9)
        if key == "income":
            ax.set_xticks([0, 50000, 100000, 150000, 200000, 250000])
            ax.xaxis.set_major_formatter(FuncFormatter(lambda value, _: f"${value/1000:.0f}k"+("+" if value == 250000 else "")))
        ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:,.0f}"))
        ax.tick_params(colors=muted, labelsize=10)
        ax.spines[["top", "right"]].set_visible(False)
        for edge in ("left", "bottom"):
            ax.spines[edge].set_color(muted)
        if leaders and column == 1:
            ax.yaxis.tick_right()
            ax.yaxis.set_label_position("right")
            ax.yaxis.label.set_rotation(270)
            ax.yaxis.label.set_verticalalignment("center")
            ax.yaxis.set_label_coords(1.105, .5)
            ax.spines["left"].set_visible(False)
            ax.spines["right"].set_visible(True)
            ax.spines["right"].set_color(muted)
        ax.grid(alpha=.15)
        rho = result["correlations"][pair]["spearman_rho"]
        fig.text(chart_left, .928 if leaders else .92, title, fontsize=18, color=ink)
        fig.text(chart_left if leaders else chart_left+chart_width, .912 if leaders else .92,
                 f"Trend strength  {rho:+.2f}", ha="left" if leaders else "right", fontsize=11, color=ink)
        if leaders:
            for row in label_rows:
                px, py = fig.transFigure.inverted().transform(ax.transData.transform((row[key], row["tree_density"])))
                edge_x = chart_left+chart_width if column == 0 else chart_left
                end_x = label_left[row["id"]] if column == 0 else label_right[row["id"]]
                # Stop just outside the dot, rather than drawing over its center.
                start_x = px + (1 if column == 0 else -1)*4/(FIG_WIDTH*72)
                fig.add_artist(Line2D([start_x, edge_x, end_x], [py, py, label_y[row["id"]]],
                    transform=fig.transFigure, color=row["color"], linewidth=.55, alpha=.48,
                    solid_capstyle="round", zorder=3))
    # One shared list: each neighborhood appears once, hottest to coolest.
    income_max = 250000.
    density_max = float(np.ceil(max(r["tree_density"] for r in examples)/1000)*1000)
    income_cmap = LinearSegmentedColormap.from_list("income_values", ["#CDBA8D", "#85601F"])
    density_cmap = LinearSegmentedColormap.from_list("density_values", ["#A8C1A3", "#216548"])
    metrics = (
        ("income", "Median income", 0, income_max, income_cmap, lambda v: f"${v/1000:.0f}k"),
        ("tree_density", "Trees/km²", 0, density_max, density_cmap, lambda v: f"{v:,.0f}"),
        ("temperature", "Surface heat", *HEAT_LIMITS, heat_cmap, lambda v: f"{v:.1f}°C"),
    )
    fig.text(.08, .705, "TWELVE NEIGHBORHOODS · HOTTER → COOLER", fontsize=11, color=ink)
    fig.text(.08, .691, f"Bar scales: income $0–${income_max/1000:.0f}k+ · trees 0–{density_max:,.0f}/km² · heat 30–44°C", fontsize=8.5, color=muted, parse_math=False)
    if heat_meta:
        fig.text(.55, .705, "Surface heat · ~100 m detail · Unshaded land = missing data", fontsize=8.5, color=muted)
        bar_ax = fig.add_axes((.55, .691, .14, .005))
        bar = fig.colorbar(plt.cm.ScalarMappable(norm=heat_norm, cmap=heat_cmap),
                          cax=bar_ax, orientation="horizontal", ticks=[30, 37, 44], extend="both")
        bar.outline.set_visible(False)
        bar.ax.tick_params(labelsize=7, colors=muted, length=2, pad=2)
        fig.text(.705, .690, "Mean surface temperature · °C", fontsize=8.5, color=muted)
    width, height = .18, .18*FIG_WIDTH/FIG_HEIGHT
    for index, row in enumerate(examples):
        tile_left = .08 + (index % 4)*.22
        bottom = .671-height - (index // 4)*.216
        tile_ax = fig.add_axes((tile_left, bottom, width, height))
        draw_map(tile_ax, row["plot_x"], row["plot_y"], row["plot_group"], row["bounds"],
                 [(("context",), "#737B76", .60), (("selected",), row["color"], .93)],
                 .85, 0, water_paths, palette=palette)
        if heat_meta:
            gx, gy, heat = row["heat_grid"]
            tile_ax.pcolormesh(gx, gy, np.ma.masked_invalid(heat), cmap=heat_cmap,
                              norm=heat_norm, shading="flat", edgecolors="none",
                              antialiased=False, rasterized=True, zorder=.7)
        tile_ax.add_collection(LineCollection(boundary_lines(row["local_land"]), colors=row["color"], linewidths=.7, alpha=.85, zorder=5))
        for spine in tile_ax.spines.values():
            spine.set_visible(False)
        display_place = row["place"].replace(", Somerville", "")
        fig.text(tile_left, bottom+height+.006, f"{row['letter']}  Near {display_place}", fontsize=11, color=row["color"])
        # Aligned labels, bars and values use three compact rows. Bar color
        # encodes the value on its shared scale, never the area's badge color.
        for metric_index, (metric, label, minimum, maximum, cmap, formatter) in enumerate(metrics):
            center = bottom-.011-metric_index*.012
            fig.text(tile_left, center, label, fontsize=8, color=muted, va="center")
            value_label = formatter(row[metric])
            if metric == "income" and row["income_bound"]:
                value_label = "$250k+" if row["income_bound"] == "upper" else "≤$2.5k"
            fig.text(tile_left+width, center, value_label, fontsize=8.5,
                     color=ink, va="center", ha="right")
            bar_ax = fig.add_axes((tile_left+.057, center-.0016, .086, .0032))
            bar_ax.barh(0, maximum-minimum, left=minimum, height=1, color="#E3E3DA", edgecolor="none")
            value = float(np.clip(row[metric], minimum, maximum))
            color = cmap(Normalize(minimum, maximum, clip=True)(value))
            if metric == "temperature":
                color = "#B98269"
            bar_ax.barh(0, value-minimum, left=minimum, height=1, color=color, edgecolor="none")
            bar_ax.plot([value, value], [-.5, .5], color=muted, linewidth=.6)
            bar_ax.set_xlim(minimum, maximum)
            bar_ax.set_ylim(-.5, .5)
            bar_ax.set_axis_off()
        b = row["bounds"]
        tile_ax.plot([b[0]+.06*side, b[0]+.06*side+.25], [b[2]+.06*side]*2, color=ink, lw=1.1, zorder=8)
        tile_ax.text(b[0]+.06*side, b[2]+.095*side, "250 m", fontsize=6.5, color=ink,
                     path_effects=[effects.withStroke(linewidth=2, foreground=palette["bg"])], zorder=9)
    fig.text(.08, .024, f"{side:g} × {side:g} km insets · Color: measured block group · Gray: surrounding trees",
             fontsize=9, color=ink)
    fig.text(.55, .024, "Trend strength (Spearman): −1 opposite · 0 no consistent trend · +1 together",
             fontsize=9, color=ink)
    wider = result["wider_sample"]
    fig.text(.08, .011, f"{result['n']} matched groups · $250k+ tied for ranks; excluded from income curve · Lines: LOWESS",
             fontsize=8.5, color=muted)
    fig.text(.55, .011, f"Wider sample: {wider['n']} groups; income–trees {wider['income_tree_spearman']:+.2f} · Tree coverage incomplete",
             fontsize=8.5, color=muted)
    fig.text(.5, -.002, "Sources: Census ACS 2020–24 · Landsat: 5 clear-sky dates, summer 2025 · Trees: Nov 2026; local inventories, © OpenStreetMap contributors, USDA National Agriculture Imagery Program · Water: MassGIS",
             ha="center", fontsize=7.8, color=muted)
    fig.text(.985, .011, "arborary.world", ha="right", fontsize=9, color=ink)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # The header sits above the original canvas, preserving chart/inset scale.
    fig.savefig(args.output, dpi=args.dpi, facecolor=palette["bg"],
                bbox_inches=Bbox.from_extents(0, -.008*FIG_HEIGHT, FIG_WIDTH, (1.095 if leaders else .98)*FIG_HEIGHT))
    if args.marker_review:
        review, axes = plt.subplots(3, 1, figsize=(22, 17), facecolor=palette["bg"])
        for review_ax, style, label in zip(axes, ("outline", "soft", "label"),
                ("Fine outline", "Soft tint", "Letters without circles")):
            for artist in marker_artists:
                artist.remove()
            marker_artists = [letter_marker(*spec, style=style) for spec in marker_specs]
            buffer = io.BytesIO()
            fig.savefig(buffer, format="png", dpi=140, facecolor=palette["bg"],
                        bbox_inches=Bbox.from_extents(.015*FIG_WIDTH, .712*FIG_HEIGHT,
                                                     .97*FIG_WIDTH, .938*FIG_HEIGHT))
            buffer.seek(0)
            review_ax.imshow(plt.imread(buffer))
            review_ax.set_title(label, loc="left", fontsize=14, color=ink, pad=8)
            review_ax.set_axis_off()
        review.subplots_adjust(left=.02, right=.98, top=.97, bottom=.015, hspace=.13)
        review.savefig(args.output.with_name(args.output.stem+"_marker_review.png"), dpi=160,
                       facecolor=palette["bg"], bbox_inches="tight", pad_inches=.2)
        plt.close(review)
    plt.close(fig)
    exclude = {"land", "local_land", "plot_x", "plot_y", "plot_group", "heat_grid"}
    save_json(args.output.with_suffix(".json"), {"analysis": str(result_path), "analysis_sha256": hashlib.sha256(result_path.read_bytes()).hexdigest(),
        "examples": [{k: v for k, v in row.items() if k not in exclude} for row in examples], "order": {"temperature_descending": [r["id"] for r in examples]},
        "income_bar": {"units": "2024 USD, median household income", "limits": [0, income_max],
                       "open_ended_values": "Upper interval displayed as $250k+; lower as ≤$2.5k"},
        "tree_density_bar": {"units": "mapped trees per square kilometre of land", "limits": [0, density_max]},
        "temperature_bar": {"units": "degrees Celsius, mean summer surface temperature", "limits": HEAT_LIMITS},
        "bar_colors": "Income uses an ochre value scale, density a green value scale; temperature bars have a constant warm color (#B98269), with length encoding temperature; independent of area colors",
        "grid": {"rows": 3, "columns": 4, "unique_areas": len(examples)},
        "layout": "Map and title header, paired charts with shared list, then drilldowns" if leaders else "Original chart row with central locator",
        "tile_size_km": side, "selection": "Six deliberate range examples (income, tree density and heat) plus six geographic context examples. Illustrative, not representative; correlations use all eligible groups.",
        "example_ranges": {metric: [min(r[metric] for r in examples), max(r[metric] for r in examples)]
                           for metric in ("income", "tree_density", "temperature")},
        "statistic_label": "Trend strength (Spearman rank correlation)", "y_axis": "Mapped trees per square kilometre of land",
        "markers": ({"style": "colored dots with leaders", "chart_area_pt2": 45, "locator_area_pt2": 20,
                     "shared_list_order": [r["id"] for r in label_rows], "shared_list_sort": "tree density descending"}
                    if leaders else {"style": args.marker_style, "chart_diameter_pt": 12, "locator_diameter_pt": 8.5,
                                     "alignment": "Visible glyph bounds centered exactly on the data coordinate"}),
        "trend_line": {"method": "robust LOWESS", "fraction": SMOOTH_FRACTION, "iterations": 3, "display_x_quantiles": [.025, .975],
                       "income_curve": "Excludes open-ended published medians; temperature curve uses all matched groups"},
        "heat_shading": heat_meta,
        "metro_locator": {"bounds": metro_bounds, "circle": circle, "markers": "Selected block group centroids"}})
    print("Saved", args.output, flush=True)


if __name__ == "__main__":
    main()
