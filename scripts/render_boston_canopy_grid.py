#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = ["duckdb", "matplotlib", "numpy", "requests", "scipy", "statsmodels", "shapely>=2.1", "rasterio", "pyproj", "pillow"]
# ///
"""Render canopy/heat posters, with optional density comparisons and larger views."""
import argparse
import copy
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patheffects as effects
from matplotlib.colors import LinearSegmentedColormap, Normalize, to_rgb
from matplotlib.lines import Line2D
from matplotlib.patches import Polygon, Rectangle
from matplotlib.path import Path as MplPath
from matplotlib.ticker import FuncFormatter, MaxNLocator
from matplotlib.transforms import Bbox
import numpy as np
from PIL import Image
from statsmodels.nonparametric.smoothers_lowess import lowess

from analyze_boston_canopy_heat import OUT
from boston_canopy_grid import augment_canopy, select_four
from boston_heat_location_audit import coverage_locations
from boston_heat_disjoint import disjoint_locations
from boston_canopy_water import apply_water_exclusion
from boston_income_heat_data import save_json
from boston_tree_heat_grid import prepare, CELL_M
from boston_tree_heat_local import augment_local, assign_regions
from render_boston_correlation_insets import COLORS
from render_boston_tree_heat_local import corners
from treelines_core import PALETTES, draw_map
from treelines_data import project_lonlat

PALETTE = {**PALETTES["light"], "bg": "#F1F3F0", "text": "#203A35",
           "muted": "#586963", "water": "#CBDDE3", "water_edge": "#A8C3CD"}
INK, MUTED, BG = (PALETTE[k] for k in ("text", "muted", "bg"))
HEAT = LinearSegmentedColormap.from_list("local_heat", [
    tuple(.58*np.array(to_rgb(c))+.42*np.array(to_rgb(BG))) for c in ("#397B96", BG, "#B84F46")])
NORM = Normalize(-6, 6, clip=True)
TREE_COLOR, TREE_ALPHA = "#303638", .88
SUBTITLE = "Do trees cut the Boston summer heat? Correlational, not causal."


def canopy_label_color(delta):
    """Percentage-point departure from the view median; neutral within rounding."""
    if abs(delta) < .5:
        return INK
    target = "#1E6845" if delta > 0 else "#A43D39"
    weight = .55+.45*min(abs(delta)/10, 1)
    return tuple(weight*np.array(to_rgb(target))+(1-weight)*np.array(to_rgb(INK)))


def score(value):
    return "n/a" if value is None else f"{value:+.2f}"


def style_axis(ax, size=10):
    ax.set_facecolor(BG)
    ax.tick_params(colors=MUTED, labelsize=size, length=2, pad=3)
    ax.spines[["top", "right"]].set_visible(False)
    for edge in ("left", "bottom"):
        ax.spines[edge].set_color(MUTED)
        ax.spines[edge].set_linewidth(.5)
    ax.grid(alpha=.13)


def overview(fig, data, views, day_count):
    ax = fig.add_axes((.035, .790, .2475, .2475*fig.get_figwidth()/fig.get_figheight()))
    circle = data["circle"]
    cx, cy = circle["centre_projected_km"]
    visible = np.hypot(data["x"]-cx, data["y"]-cy) <= circle["fade_end_km"]
    palette = {**PALETTE, **{k: tuple(.60*np.array(to_rgb(PALETTE[k]))+.40*np.array(to_rgb(BG)))
                             for k in ("water", "water_edge")}}
    draw_map(ax, data["x"][visible], data["y"][visible], data["points"]["data_source"][visible],
             data["metro_bounds"], [(tuple(np.unique(data["points"]["data_source"])), "#216548", .12)],
             .035, 0, data["water_paths"], circle, palette)
    edges = {}
    for v in views:
        xy = corners(v["bounds_utm"])
        lon, lat = data["inverse"].transform(xy[:, 0], xy[:, 1])
        px, py = project_lonlat(lon, lat, data["projection"])
        ax.add_patch(Polygon(np.column_stack([px, py]), closed=True, fill=False,
                             edgecolor=v["color"], lw=.9, alpha=.8, zorder=8))
        edges[v["place"]] = ((px[1]+px[2])/2, (py[1]+py[2])/2)
    fig.text(.295, .930, "BOSTON, MA, USA", fontsize=14, color=INK)
    fig.text(.295, .916, "Warmer → cooler views", fontsize=10.5, color=MUTED)
    fig.text(.500, .922, f"Avg. {day_count}-day\nland heat", fontsize=10, color=INK,
             ha="right", va="top", linespacing=1.05)
    pos = np.linspace(.902, .799, len(views)) if len(views)>4 else np.linspace(.902, .809, len(views))
    fig.canvas.draw()
    for v, yy in zip(views, pos):
        fig.text(.300, yy, f"{v['letter']}  {v['place']}", fontsize=12, color=v["color"], va="center")
        fig.text(.500, yy, f"{v['baseline_temperature']:.1f}°C", fontsize=11, color=MUTED, ha="right", va="center")
        px, py = fig.transFigure.inverted().transform(ax.transData.transform(edges[v["place"]]))
        fig.add_artist(Line2D([px, .278, .291], [py, py, yy], transform=fig.transFigure,
                             color=v["color"], lw=.6, alpha=.45, zorder=3))


def draw_headline_axis(chart, rows, views, metric="canopy_percent", point_size=22, tick_size=10):
    """Shared observations and smooth trend; layout and callouts belong to the caller."""
    owners = assign_regions(rows, views)
    colors = {v["place"]: v["color"] for v in views}
    point_colors = np.array([colors.get(owners[c["id"]], "#B5BDB3") for c in rows])
    xs = np.array([c["temperature"] for c in rows])
    ys = np.array([c[metric] for c in rows])
    xlim = (np.floor(xs.min()), np.ceil(xs.max()))
    step = 2000 if metric == "tree_density" else 10
    chart.set_ylim(0, np.ceil(ys.max()/step)*step)
    chart.scatter(xs, ys, s=point_size, c=point_colors, alpha=.82, edgecolors="none", zorder=3)
    sx = np.linspace(*np.quantile(xs, [.025, .975]), 240)
    sy = lowess(ys, xs, frac=.6, it=3, xvals=sx)
    chart.plot(sx, sy, color="#89978C", lw=1.2)
    chart.set_xlim(xlim)
    style_axis(chart, tick_size)
    if metric == "tree_density":
        chart.yaxis.set_major_formatter(FuncFormatter(lambda val, _: f"{val/1000:g}k"))
    return owners


def headline_charts(fig, rows, views, comparisons, canopy_only=False):
    specs = (("canopy_percent", .575, "Estimated canopy · % land"),) if canopy_only else (
        ("tree_density", .550, "Trees / km² land"), ("canopy_percent", .796, "Estimated canopy · % land"))
    chart_width = .390 if canopy_only else .183
    for metric, left, label in specs:
        a = fig.add_axes((left, .805, chart_width, .130))
        owners = draw_headline_axis(a, rows, views, metric)
        a.set_xlabel("Summer surface heat · °C", fontsize=12, color=INK)
        a.set_ylabel(label, fontsize=12, color=INK, labelpad=7)
        a.text(.97, .96, f"Correlation {score(comparisons[metric]['spearman'])}",
               transform=a.transAxes, fontsize=18 if canopy_only else 13, color=INK, ha="right", va="top",
               path_effects=[effects.withStroke(linewidth=3, foreground=BG)])
    sample_label = (f"{len(rows)} qualifying squares · Spearman correlation" if canopy_only else
                    f"Same {len(rows)} squares in both plots · Spearman correlation")
    fig.text(.765, .775, sample_label,
             fontsize=10.5, ha="center", color=MUTED)
    return owners


def inset_water(data):
    p = data["projection"]
    kx = 111.195*np.cos(np.radians(p["central_latitude"]))
    paths = []
    for path in data["water_paths"]:
        lon = path.vertices[:, 0]/kx+p["central_longitude"]
        lat = path.vertices[:, 1]/111.195+p["central_latitude"]
        we, wn = data["forward"].transform(lon, lat)
        paths.append(MplPath(np.column_stack([we, wn])/1000, path.codes))
    return paths


def draw_tile_legend(fig, data, views, water):
    """An actual 500m cell, with leaders pointing to its values and a real tree."""
    view = next((v for v in views if v["place"] == "Corey Hill Park"), views[0])
    cell = min(view["paired_cells"], key=lambda c: (abs(c["temperature_delta"]+2)
               +.1*abs(c["canopy_percent"]-24), c["id"]))
    w, s = cell["west"], cell["south"]
    left, bottom, width = .060, .711, .084
    height = width*fig.get_figwidth()/fig.get_figheight()
    ax = fig.add_axes((left, bottom, width, height))
    use = ((data["te"]>=w) & (data["te"]<w+CELL_M)
           & (data["tn"]>=s) & (data["tn"]<s+CELL_M))
    draw_map(ax, data["te"][use]/1000, data["tn"][use]/1000, np.full(use.sum(), "trees"),
             [w/1000, (w+CELL_M)/1000, s/1000, (s+CELL_M)/1000],
             [(("trees",), TREE_COLOR, TREE_ALPHA)], .9, 0, water, palette=PALETTE)
    mask = ((data["east"]>=w) & (data["east"]<=w+CELL_M)
            & (data["north"]>=s) & (data["north"]<=s+CELL_M))
    rr, cc = np.where(mask)
    r0, r1 = max(0, rr.min()-1), min(data["mean"].shape[0], rr.max()+2)
    c0, c1 = max(0, cc.min()-1), min(data["mean"].shape[1], cc.max()+2)
    gr, gc = np.meshgrid(np.arange(r0, r1+1), np.arange(c0, c1+1), indexing="ij")
    ge, gn = data["affine"]*(gc, gr)
    ax.pcolormesh(ge/1000, gn/1000, np.ma.masked_invalid(data["mean"][r0:r1, c0:c1]-view["baseline_temperature"]),
                  cmap=HEAT, norm=NORM, shading="flat", edgecolors="none", antialiased=False, zorder=.7)
    ax.add_patch(Rectangle((w/1000, s/1000), .5, .5, fill=False, edgecolor=MUTED, lw=.8, zorder=6))
    median = float(np.median([c["canopy_percent"] for c in view["paired_cells"]]))
    delta = cell["temperature_delta"]
    for value, yy, color in ((f"{cell['canopy_percent']:.0f}%", .65, canopy_label_color(cell["canopy_percent"]-median)),
                             (f"{delta:+.1f}°C", .40, "#316E51" if delta<0 else "#A43D39")):
        ax.text(.49, yy, value, transform=ax.transAxes, ha="center", va="center", fontsize=18,
                color=color, zorder=8, path_effects=[effects.withStroke(linewidth=3.5, foreground=BG)])
    # Highlight one actual mapped point, not a synthetic example dot.
    ex, ny = (data["te"][use]-w)/CELL_M, (data["tn"][use]-s)/CELL_M
    index = int(np.argmin((ex-.78)**2+(ny-.17)**2))
    ax.scatter([data["te"][use][index]/1000], [data["tn"][use][index]/1000],
               s=42, facecolors="none", edgecolors=INK, linewidths=.8, zorder=9)
    fig.text(left, .784, "Legend", fontsize=18, color=INK)
    fig.text(left+width/2, .701, "500 m × 500 m", fontsize=10, ha="center", color=MUTED)
    labels = [(.65, .766, "Crown coverage", "Estimated % of land under tree crowns"),
              (.40, .738, "Heat delta", "°C above / below the view’s mean"),
              (float(ny[index]), .710, "Tree dots", None)]
    for i, (yy, target, title, detail) in enumerate(labels):
        start_x = left+width*(float(ex[index]) if i==2 else .66)
        start_y = bottom+height*yy
        fig.add_artist(Line2D([start_x, .168, .195], [start_y, start_y, target],
                             transform=fig.transFigure, color=MUTED, lw=.7, zorder=10))
        fig.text(.203, target, title, fontsize=13, color=INK, va="center")
        if detail:
            fig.text(.203, target-.010, detail, fontsize=10, color=MUTED, va="center")
    fig.text(.203, .747, "Green % above / red % below the view median", fontsize=10, color=MUTED)
    fig.text(.203, .719, "Green = cooler · red = warmer", fontsize=10, color=MUTED)
    return {"place": view["place"], "cell_id": cell["id"], "canopy_percent": cell["canopy_percent"],
            "temperature_delta": delta, "median_canopy_percent": median,
            "tree_dot": "Leader highlights an actual mapped tree in the selected cell"}


def local_map(fig, v, data, cells, water, left, bottom, width, height, large, canopy_only=False,
              *, typography=None, captions=True):
    typography = typography or {}
    label_size = typography.get("label_size", 13 if large else 11)
    gap_size = typography.get("gap_size", 10 if large else 8.5)
    dot_size = typography.get("dot_size", 1.0 if large else .72)
    label_offset = typography.get("label_offset", 8.3 if large else 7)
    ax = fig.add_axes((left, bottom, width, height))
    w, s, e, n = v["bounds_utm"]
    use = (data["te"]>=w) & (data["te"]<e) & (data["tn"]>=s) & (data["tn"]<n)
    draw_map(ax, data["te"][use]/1000, data["tn"][use]/1000, np.full(use.sum(), "trees"),
             [w/1000, e/1000, s/1000, n/1000], [(("trees",), TREE_COLOR, TREE_ALPHA)],
             dot_size, 0, water, palette=PALETTE)
    mask = (data["east"]>=w) & (data["east"]<=e) & (data["north"]>=s) & (data["north"]<=n)
    rr, cc = np.where(mask)
    r0, r1 = max(0, rr.min()-1), min(data["mean"].shape[0], rr.max()+2)
    c0, c1 = max(0, cc.min()-1), min(data["mean"].shape[1], cc.max()+2)
    gr, gc = np.meshgrid(np.arange(r0, r1+1), np.arange(c0, c1+1), indexing="ij")
    ge, gn = data["affine"]*(gc, gr)
    ax.pcolormesh(ge/1000, gn/1000, np.ma.masked_invalid(data["mean"][r0:r1, c0:c1]-v["baseline_temperature"]),
                  cmap=HEAT, norm=NORM, shading="flat", edgecolors="none", antialiased=False,
                  rasterized=True, zorder=.7)
    paired = {c["id"] for c in v["paired_cells"]}
    canopy_median = float(np.median([c["canopy_percent"] for c in v["paired_cells"]]))
    v["canopy_label_median_percent"] = canopy_median
    for xx in np.arange(w, e, CELL_M):
        for yy in np.arange(s, n, CELL_M):
            c = cells.get((xx, yy))
            good = c is not None and c["id"] in paired
            ax.add_patch(Rectangle((xx/1000, yy/1000), .5, .5, fill=False,
                                  edgecolor=INK, lw=.4, alpha=.40 if good else .18,
                                  linestyle="-" if good else ":", zorder=6))
            center = ((xx+CELL_M/2)/1000, (yy+CELL_M/2)/1000)
            if c is not None and c.get("water_excluded"):
                ax.add_patch(Rectangle((xx/1000, yy/1000), .5, .5,
                                      facecolor=BG, edgecolor="none", alpha=.82, zorder=1.9))
                percent = 100*(1-c["land_fraction"])
                water_label = f"{percent:.1f}" if percent < 51 else f"{percent:.0f}"
                ax.text(*center, f"{water_label}% water\nexcluded", ha="center", va="center",
                        fontsize=typography.get("water_size", 10 if large else 9), color="#455967", zorder=8,
                        path_effects=[effects.withStroke(linewidth=2, foreground=BG)])
            elif good:
                delta = c["temperature"]-v["baseline_temperature"]
                if abs(delta)<.05:
                    delta = 0.
                color = "#316E51" if delta<0 else "#A43D39" if delta>0 else MUTED
                offset = label_offset
                labels = [(f"{c['canopy_percent']:.0f}%", offset, canopy_label_color(c["canopy_percent"]-canopy_median)),
                          (f"{delta:+.1f}°C", -offset, color)]
                for label, dy, ink in labels:
                    ax.annotate(label, center, xytext=(0, dy), textcoords="offset points", ha="center", va="center",
                                fontsize=label_size, color=ink, zorder=8,
                                path_effects=[effects.withStroke(linewidth=3.1 if large else 2.7, foreground=BG)])
            elif c is None or c["land_km2"]>0:
                label = ("Outside study" if c is None else "Crown sizes\nmissing" if c["eligible"] else
                         "Heat gaps" if c["heat_coverage"]<.9 else "Limited heat")
                ax.text(*center, label, ha="center", va="center", fontsize=gap_size, color=MUTED, zorder=8,
                        path_effects=[effects.withStroke(linewidth=2, foreground=BG)])
    if captions:
        fig.text(left, bottom+height+.008, f"{v['letter']}  {v['place']}", fontsize=21 if large else 17, color=v["color"])
        fig.text(left, bottom-.008,
                 f"Mean {v['baseline_temperature']:.1f}°C · {v['paired_n']}/{len(v['all_cell_ids'])} cells · heat {v['baseline_valid_land_fraction']:.0%}",
                 fontsize=11 if large else 10, color=MUTED, va="center")
    return ax


def local_charts(fig, v, left, bottom, width, large, canopy_only=False, delta_limit=6.5):
    h = .043 if large else .026
    gap = .042 if large else .030
    each = width-.018 if canopy_only else (width-gap-.018)/2
    specs = (("canopy_percent", "Canopy · %"),) if canopy_only else (
        ("tree_density", "Trees / km²"), ("canopy_percent", "Canopy · %"))
    for j, (metric, label) in enumerate(specs):
        a = fig.add_axes((left+.018+j*(each+gap), bottom-.060 if large else bottom-.043, each, h))
        rows = v["paired_cells"]
        dx = [c["temperature_delta"] for c in rows]
        dy = [c[metric] for c in rows]
        a.scatter(dx, dy, s=30 if large else 19, color=v["color"], edgecolor=BG, lw=.4, zorder=3)
        a.axvline(0, color=MUTED, lw=.6, ls=(0, (2, 3)), alpha=.6)
        step = 1000 if metric == "tree_density" else 5
        span = max(float(np.ptp(dy)), step)
        ylo = max(0., float(np.floor((min(dy)-.10*span)/step)*step))
        yhi = float(np.ceil((max(dy)+max(.25*span, step))/step)*step)
        a.set(xlim=(-delta_limit, delta_limit), ylim=(ylo, yhi), xticks=[-6, 0, 6])
        a.yaxis.set_major_locator(MaxNLocator(nbins=3, integer=True))
        v.setdefault("plot_y_limits", {})[metric] = [ylo, yhi]
        a.set_ylabel(label, fontsize=10 if large else 9, color=MUTED, labelpad=3)
        a.xaxis.set_major_formatter(FuncFormatter(lambda val, _: f"{val:+.0f}°" if val else "0°"))
        if metric=="tree_density":
            a.yaxis.set_major_formatter(FuncFormatter(lambda val, _: f"{val/1000:g}k" if val else "0"))
        style_axis(a, 9)
        a.text(.97, .96, f"Correlation {score(v['paired_associations'][metric]['spearman'])}",
               transform=a.transAxes, ha="right", va="top", fontsize=12 if large else 10.5, color=INK, zorder=10,
               path_effects=[effects.withStroke(linewidth=2.5, foreground=BG)])


def render(result, data, rows, views, output, dpi, large, canopy_only=False):
    fig = plt.figure(figsize=(24, 38 if large else 33), facecolor=BG)
    columns = 2 if large else 4
    width = .39 if large else .205
    grid_margin = .05
    column_step = (1-2*grid_margin-width)/(columns-1)
    methods_center = .5
    fig.text(.50, .985, "Summertime Shadeness", fontsize=54, ha="center", va="center", color=INK)
    fig.text(.50, .960, SUBTITLE, fontsize=20,
             ha="center", va="center", color=MUTED)
    day_count = len(result["provenance"]["dates"])
    overview(fig, data, views, day_count)
    owners = headline_charts(fig, rows, views, result["canopy_comparison"]["associations"], canopy_only)
    top = .685
    cax = fig.add_axes((.81, .737, .15, .004))
    cb = fig.colorbar(plt.cm.ScalarMappable(norm=NORM, cmap=HEAT), cax=cax, orientation="horizontal", ticks=[-6, 0, 6], extend="both")
    cb.outline.set_visible(False)
    cb.ax.tick_params(labelsize=10, colors=MUTED, length=2, pad=2)
    fig.text(.96, .758, "Background shading", fontsize=13, color=INK, ha="right")
    fig.text(.96, .747, "Cooler ← Δ°C → Warmer", fontsize=10, color=MUTED, ha="right")
    fig.text(.96, .714, "Relative to each view’s mean", fontsize=10, color=MUTED, ha="right")
    methods_texts = [
        fig.text(.50, .765, "WHAT THIS SHOWS", fontsize=14, color=INK, ha="center"),
        fig.text(.50, .751, f"{day_count} clear-sky days · summer 2025", fontsize=12, color=INK, ha="center"),
        fig.text(.50, .739, "Surface Heat (Not air)", fontsize=11, color=MUTED, ha="center"),
        fig.text(.50, .727, "Estimated crowns; overlaps merged", fontsize=11, color=MUTED, ha="center"),
        fig.text(.50, .715, "500 m cells; >50% water excluded", fontsize=11, color=MUTED, ha="center"),
    ]
    cells = {(c["west"], c["south"]): c for c in result["cells"]}
    water = inset_water(data)
    legend_text_start = len(fig.texts)
    legend = draw_tile_legend(fig, data, views, water)
    # Anchor the heading AND its body to the whole poster's horizontal midpoint.
    # In the twelve-view grid this is between Nubian Square and Brighton Center.
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    legend_right = max(t.get_window_extent(renderer).x1 for t in fig.texts[legend_text_start:])
    heat_left = cax.get_tightbbox(renderer).x0
    block_width = max(t.get_window_extent(renderer).width for t in methods_texts)
    if block_width >= heat_left-legend_right:
        raise ValueError("Methods block does not fit between legend and heat scale")
    block_center = fig.transFigure.transform((methods_center, 0))[0]
    if block_center-block_width/2 <= legend_right or block_center+block_width/2 >= heat_left:
        raise ValueError("Poster-centered methods text overlaps the legend or heat scale")
    for text in methods_texts:
        text.set_x(methods_center)
    methods_alignment = {"rule": "Heading and body center-aligned on the whole poster's horizontal midpoint",
                         "center_figure_fraction": methods_center}
    height = width*fig.get_figwidth()/fig.get_figheight()
    delta_limit = max(6.5, float(np.ceil(max(abs(c["temperature_delta"])
                         for v in views for c in v["paired_cells"]))+.5))
    for i, v in enumerate(views):
        left = grid_margin+(i % columns)*column_step
        bottom = top-height-(i//columns)*(.330 if large else .218)
        local_map(fig, v, data, cells, water, left, bottom, width, height, large, canopy_only)
        local_charts(fig, v, left, bottom, width, large, canopy_only, delta_limit)
    if large:
        note = "Selected for ≥90% view heat coverage, ≥18 paired cells and strong inverse correlations in both metrics; illustrative, not representative."
    elif "location_audit" in result:
        note = "Tiles selected for available heat coverage · Crown sizes available for ≥95% of mapped trees per measured cell"
    elif canopy_only:
        note = "All 12 landmark views · Measured squares have crown widths for ≥95% of mapped tree centers · Correlation n/a for fewer than 6 cells"
    else:
        note = "All 12 landmark views · Same cells feed each pair of correlations; ≥95% of mapped tree centers have crown widths."
    fig.text(.50, .032, note, ha="center", fontsize=10, color=INK)
    spatial_note = "Non-overlapping 2 × 2 km views" if result.get("view_size_m") == 2000 else "Overlapping views"
    fig.text(.50, .024, f"Canopy: estimated crown union, overlaps counted once · Source widths + DBH-model fallback · Incomplete trees · {spatial_note}",
             ha="center", fontsize=10, color=MUTED)
    fig.text(.50, .016, "≥90% common land-heat coverage · A clear-sky sample, not an all-weather summer mean · Mini-plots: Δ°C on X; Y-scales vary",
             ha="center", fontsize=10, color=MUTED)
    fig.text(.50, .007, "USGS Landsat 2025 · Nov 2026 trees: inventories, © OpenStreetMap, USDA National Agriculture Imagery Program · Water: MassGIS",
             ha="center", fontsize=9.5, color=MUTED)
    fig.text(.965, .032, "arborary.world", ha="right", fontsize=10, color=INK)
    output.parent.mkdir(parents=True, exist_ok=True)
    # Tight cropping otherwise removes any whitespace added above the title.
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    center_errors = [abs((t.get_window_extent(renderer).x0+t.get_window_extent(renderer).x1)/2-block_center)
                     for t in methods_texts]
    if max(center_errors) > .5:
        raise ValueError("Methods heading/body are not centered over the map column")
    methods_alignment["max_center_error_output_pixels"] = max(center_errors)*dpi/fig.dpi
    crop = fig.get_tightbbox(fig.canvas.get_renderer()).padded(.2)
    # Preserve the full page width: asymmetric tight cropping otherwise shifts
    # the page center and changes its left/right margins after layout.
    crop = Bbox.from_extents(0, crop.y0, fig.get_figwidth(), crop.y1+.45)
    fig.savefig(output, dpi=dpi, facecolor=BG, bbox_inches=crop)
    plt.close(fig)
    with Image.open(output) as image:
        output_size = image.size
        image.thumbnail((1600, 2400))
        image.save(output.with_name(output.stem+"_preview.png"))
    report = copy.deepcopy(result)
    report["render"] = {"target": ("four-large" if large else "all-twelve") + ("-canopy" if canopy_only else "-comparison"), "output": str(output),
                        "metrics_shown": ["canopy_percent"] if canopy_only else ["tree_density", "canopy_percent"],
                        "title": "Summertime Shadeness", "subtitle": SUBTITLE,
                        "canopy_label_color": "Percentage-point departure from the median canopy percent of measured 500m cells in each view; green above, red below; neutral within 0.5pp; saturation at 10pp",
                        "paper_color": BG, "heat_palette": "Blue/neutral/red; 58% chromatic blend with cool mineral-white paper",
                        "legend": legend, "tree_dots": {"color": TREE_COLOR, "alpha": TREE_ALPHA},
                        "output_pixels": output_size, "dpi": dpi, "local_dot_area_pt2": 1.0 if large else .72,
                        "map_letters": False, "list_heat_header": f"Avg. {day_count}-day land heat",
                        "methods_alignment": methods_alignment,
                        "page_horizontal_crop_inches": [0, fig.get_figwidth()],
                        "map_grid_margins_fraction": [grid_margin, grid_margin],
                        "map_grid_margins_output_pixels": [grid_margin*fig.get_figwidth()*dpi]*2,
                        "extra_top_margin_inches": .45, "overview_width_fraction": .2475,
                        "local_y_scaling": "Independent per inset: data range with padding, rounded to 5 percentage points (or 1000 trees/km2 for comparison mode)",
                        "missing_crown_label": "Crown sizes missing: fewer than 95% of mapped land tree centers have a source or fallback crown width; not an imagery gap",
                        "views": [v["place"] for v in views], "global_region_owners": owners,
                        "local_x": "Temperature delta from view mean, C", "map_labels": "Estimated canopy percent of land; temperature delta",
                        "local_xlim": [-delta_limit, delta_limit],
                        "water_exclusions": ">50% water area", "global_same_sample": True, "local_same_sample": True}
    save_json(output.with_suffix(".json"), report)
    print("Saved", output, flush=True)


def prepare_poster(locations="disjoint"):
    """One data/selection pipeline shared by desktop and mobile compositions."""
    result, data = prepare(min_land_fraction=0.)
    views = augment_local(result, data)
    rows, _ = augment_canopy(result, data, views)
    if locations == "coverage":
        views = coverage_locations(result, data, views)
    elif locations == "disjoint":
        views = disjoint_locations(result, data, views)
    rows = apply_water_exclusion(result, views)
    for i, v in enumerate(views):
        v["color"] = COLORS[i]
    for key in ("examples", "pair_selection", "selection_warning"):
        result.pop(key, None)
    return result, data, rows, views


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dpi", type=int, default=300)
    parser.add_argument("--regions", choices=("all", "four", "both"), default="all")
    parser.add_argument("--metric", choices=("canopy", "comparison"), default="canopy",
                        help="Canopy-only by default; comparison retains the earlier density charts")
    parser.add_argument("--locations", choices=("disjoint", "coverage", "original"), default="disjoint",
                        help="Disjoint 2 km views by default; coverage/original retain the earlier 2.5 km targets")
    parser.add_argument("--output-dir", type=Path, default=OUT)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    if args.locations == "disjoint" and args.regions != "all":
        parser.error("Disjoint selection is a twelve-view target; use --locations coverage for the legacy four-view target")
    result, data, rows, views = prepare_poster(args.locations)
    four = select_four(views) if args.locations != "disjoint" else []
    result["canopy_comparison"]["selected_four"] = [v["place"] for v in four]
    if args.locations in ("coverage", "disjoint"):
        save_json(args.output_dir/"boston_heat_location_audit.json", result["location_audit"])
    canopy_only = args.metric == "canopy"
    if args.regions in ("all", "both"):
        filename = "boston_canopy_heat_twelve_regions.png" if canopy_only else "boston_canopy_density_heat_comparison.png"
        if args.locations == "original":
            filename = filename.replace(".png", "_original_locations.png")
        render(result, data, rows, views, args.output_dir/filename, args.dpi, False, canopy_only)
    if args.regions in ("four", "both"):
        filename = "boston_canopy_heat_four_regions.png" if canopy_only else "boston_canopy_density_heat_four_regions.png"
        render(result, data, rows, four, args.output_dir/filename, args.dpi, True, canopy_only)


if __name__ == "__main__":
    main()
