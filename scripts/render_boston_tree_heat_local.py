#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = ["duckdb", "matplotlib", "numpy", "requests", "scipy", "statsmodels", "shapely>=2.1", "rasterio", "pyproj"]
# ///
"""Boston trees and local heat: normalized landmark views and grid associations."""
import argparse
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patheffects as effects
from matplotlib.colors import LinearSegmentedColormap, Normalize, to_rgb
from matplotlib.lines import Line2D
from matplotlib.patches import Polygon, Rectangle
from matplotlib.path import Path as MplPath
from matplotlib.ticker import FuncFormatter
import numpy as np
from statsmodels.nonparametric.smoothers_lowess import lowess

from analyze_boston_canopy_heat import OUT
from boston_income_heat_data import save_json
from boston_tree_heat_grid import prepare, CELL_M
from boston_tree_heat_local import augment_local, assign_regions
from render_boston_correlation_insets import COLORS
from treelines_core import PALETTES, draw_map
from treelines_data import project_lonlat

HOT, COOL = "#B5553C", "#397888"


def corners(bounds):
    w, s, e, n = bounds
    return np.array([[w, s], [e, s], [e, n], [w, n]])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dpi", type=int, default=220)
    parser.add_argument("--output", type=Path, default=OUT/"boston_tree_heat_local_post.png")
    parser.add_argument("--water-context", action=argparse.BooleanOptionalAction, default=False,
                        help="Opt in to exploratory nearby-water colors and correlations in mini-plots")
    args = parser.parse_args()
    result, data = prepare(min_land_fraction=0.)
    rows = [r for r in result["cells"] if r["eligible"]]
    examples = augment_local(result, data)
    result.pop("examples", None)
    result.pop("pair_selection", None)
    result.pop("selection_warning", None)
    for i, r in enumerate(examples):
        r["color"] = COLORS[i]
    palette = PALETTES["light"]
    ink, muted, bg = palette["text"], palette["muted"], palette["bg"]
    fig = plt.figure(figsize=(22, 31), facecolor=bg)
    fig.text(.50, .974, "Boston, MA, USA · Trees & summer heat", fontsize=34,
             ha="center", va="center", color=ink)
    metro = fig.add_axes((.035, .748, .255, .255*22/31))
    circle = data["circle"]
    cx, cy = circle["centre_projected_km"]
    visible = np.hypot(data["x"]-cx, data["y"]-cy) <= circle["fade_end_km"]
    overview_palette = {**palette, **{k: tuple(.60*np.array(to_rgb(palette[k]))
                        + .40*np.array(to_rgb(bg))) for k in ("water", "water_edge")}}
    draw_map(metro, data["x"][visible], data["y"][visible], data["points"]["data_source"][visible],
        data["metro_bounds"], [(tuple(np.unique(data["points"]["data_source"])), "#216548", .12)],
        .035, 0, data["water_paths"], circle, overview_palette)
    box_edges = {}
    for r in examples:
        xy = corners(r["bounds_utm"])
        lon, lat = data["inverse"].transform(xy[:, 0], xy[:, 1])
        px, py = project_lonlat(lon, lat, data["projection"])
        box_edges[r["place"]] = ((px[1]+px[2])/2, (py[1]+py[2])/2)
        metro.add_patch(Polygon(np.column_stack([px, py]), closed=True, fill=False,
                                edgecolor=r["color"], lw=.8, alpha=.80, zorder=8))
        metro.text(px[3]+.03, py[3]+.07, r["letter"], fontsize=8, color=r["color"], zorder=9,
                   path_effects=[effects.withStroke(linewidth=2, foreground=bg)])
    fig.text(.1625, .737, "Boxes locate the 2.5 km landmark views below", ha="center", fontsize=8, color=muted)

    xs = np.array([r["temperature"] for r in rows])
    ys = np.array([r["tree_density"] for r in rows])
    upper_n = int((ys > 8000).sum())
    high_max = float(np.ceil(ys.max()/2000)*2000)
    xlim = (np.floor(xs.min()), np.ceil(xs.max()))
    ax = fig.add_axes((.625, .765, .335, .125), facecolor=bg)
    high = fig.add_axes((.625, .900, .335, .030), facecolor=bg, sharex=ax)
    water_cmap = LinearSegmentedColormap.from_list("water_context", ["#8A9469", "#397D9C"])
    water_norm = Normalize(0, 15, clip=True)
    owners = assign_regions(rows, examples)
    region_colors = {r["place"]: r["color"] for r in examples}
    point_colors = [region_colors.get(owners[r["id"]], "#B5BDB3") for r in rows]
    for chart, ylim in ((ax, (0, 8000)), (high, (8000, high_max+400))):
        in_band = ys <= 8000 if chart is ax else ys > 8000
        chart.scatter(xs[in_band], ys[in_band], s=18, c=np.array(point_colors)[in_band],
                      alpha=.78, edgecolors="none", zorder=3)
        chart.set(xlim=xlim, ylim=ylim)
        chart.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:,.0f}"))
        chart.tick_params(colors=muted, labelsize=9)
        chart.spines[["top", "right"]].set_visible(False)
        for edge in ("left", "bottom"):
            chart.spines[edge].set_color(muted)
        chart.grid(alpha=.15)
    high.set_yticks([12000, 16000])
    high.spines["bottom"].set_visible(False)
    high.tick_params(axis="x", bottom=False, labelbottom=False)
    # Adjacent ranges with different physical scales; every observation is kept.
    for a, y0 in ((ax, 1), (high, 0)):
        a.plot([-.012, .012], [y0-.018, y0+.018], transform=a.transAxes,
               color=muted, lw=.8, clip_on=False)
    high.text(.98, .80, f"{upper_n} higher-density squares · compressed scale", ha="right",
              va="top", transform=high.transAxes, fontsize=7.5, color=muted)
    fig.text(.582, .844, "Mapped trees / km² land", rotation=90, ha="center", va="center", fontsize=10, color=ink)
    ax.set_xlabel("Mean summer surface temperature · °C", fontsize=10, color=ink)
    sx = np.linspace(*np.quantile(xs, [.025, .975]), 240)
    sy = lowess(ys, xs, frac=.6, it=3, xvals=sx)
    for a in (ax, high):
        a.plot(sx, sy, color="#89978C", lw=1.4)
    fig.text(.355, .932, "LOCAL MEAN HEAT", fontsize=10, color=muted)
    fig.text(.355, .920, "Warmer → cooler views", fontsize=8.5, color=muted)
    list_y = {}
    for r, yy in zip(examples, np.linspace(.905, .776, len(examples))):
        list_y[r["place"]] = yy
        fig.text(.365, yy, f"{r['letter']}   {r['place']}", fontsize=9.5, color=r["color"], va="center")
        fig.text(.546, yy, f"{r['baseline_temperature']:.1f}°C", fontsize=8.5, color=muted, ha="right", va="center")
    fig.canvas.draw()
    for r in examples:
        px, py = fig.transFigure.inverted().transform(metro.transData.transform(box_edges[r["place"]]))
        fig.add_artist(Line2D([px, .305, .355], [py, py, list_y[r["place"]]],
                             transform=fig.transFigure, color=r["color"], lw=.6, alpha=.45, zorder=3))
    fig.text(.625, .738, "Point colors: landmark views · Gray: elsewhere", fontsize=8, color=muted)
    fig.text(.08, .718, "LOCAL HEAT · EACH VIEW CENTERED ON ITS OWN MEAN", fontsize=11, color=ink)
    fig.text(.08, .706, "Grid labels: tree density (trees/km²) + Δ°C · Mini-plots: Δ°C on X, trees/km² on Y", fontsize=8.5, color=muted)
    heat_colors = [tuple(.40*np.array(to_rgb(c))+.60*np.array(to_rgb(bg))) for c in ("#3E8098", bg, "#B94E32")]
    cmap = LinearSegmentedColormap.from_list("local_heat", heat_colors)
    norm = Normalize(-6, 6, clip=True)
    legends = [(.58, cmap, norm, [-6, 0, 6], "Cooler ← Δ°C from local mean → Warmer")]
    if args.water_context:
        legends.append((.79, water_cmap, water_norm, [0, 5, 10, 15], "Mini-plot color: nearby water · %"))
    for left, cm, nm, ticks, label in legends:
        fig.text(left, .719, label, fontsize=8, color=muted)
        cax = fig.add_axes((left, .708, .13, .004))
        cb = fig.colorbar(plt.cm.ScalarMappable(norm=nm, cmap=cm), cax=cax,
                         orientation="horizontal", ticks=ticks, extend="both" if left < .7 else "max")
        cb.outline.set_visible(False)
        cb.ax.tick_params(labelsize=7, colors=muted, length=2, pad=2)

    # Reproject the existing correctly oriented water paths to the inset CRS.
    p = data["projection"]
    kx = 111.195*np.cos(np.radians(p["central_latitude"]))
    inset_water = []
    for path in data["water_paths"]:
        lon = path.vertices[:, 0]/kx+p["central_longitude"]
        lat = path.vertices[:, 1]/111.195+p["central_latitude"]
        we, wn = data["forward"].transform(lon, lat)
        inset_water.append(MplPath(np.column_stack([we, wn])/1000, path.codes))
    width, height = .19, .19*22/31
    all_cells = {(c["west"], c["south"]): c for c in result["cells"]}
    density_max = float(np.ceil(max(c["tree_density"] for v in examples for c in v["cells"])/2000)*2000)
    for i, r in enumerate(examples):
        left = .08+(i % 4)*.22
        bottom = .684-height-(i//4)*.210
        a = fig.add_axes((left, bottom, width, height))
        w, south, e, n = r["bounds_utm"]
        use = (data["te"]>=w) & (data["te"]<e) & (data["tn"]>=south) & (data["tn"]<n)
        bounds = [w/1000, e/1000, south/1000, n/1000]
        draw_map(a, data["te"][use]/1000, data["tn"][use]/1000, np.full(use.sum(), "trees"), bounds,
                 [(("trees",), "#647966", .55)], .65, 0, inset_water, palette=palette)
        mask = (data["east"]>=w) & (data["east"]<=e) & (data["north"]>=south) & (data["north"]<=n)
        rr, cc = np.where(mask)
        r0, r1 = max(0, rr.min()-1), min(data["mean"].shape[0], rr.max()+2)
        c0, c1 = max(0, cc.min()-1), min(data["mean"].shape[1], cc.max()+2)
        gr, gc = np.meshgrid(np.arange(r0, r1+1), np.arange(c0, c1+1), indexing="ij")
        ge, gn = data["affine"]*(gc, gr)
        a.pcolormesh(ge/1000, gn/1000, np.ma.masked_invalid(data["mean"][r0:r1, c0:c1]-r["baseline_temperature"]),
                    cmap=cmap, norm=norm, shading="flat", edgecolors="none", antialiased=False,
                    rasterized=True, zorder=.7)
        lookup = {(c["west"], c["south"]): c for c in r["cells"]}
        for xx in np.arange(w, e, CELL_M):
            for yy in np.arange(south, n, CELL_M):
                cell = lookup.get((xx, yy))
                a.add_patch(Rectangle((xx/1000, yy/1000), .5, .5, fill=False,
                                     edgecolor=ink, lw=.35, alpha=.40 if cell else .18,
                                     linestyle="-" if cell else ":", zorder=6))
                if cell:
                    inside = ((data["te"][use]>=xx) & (data["te"][use]<xx+CELL_M)
                              & (data["tn"][use]>=yy) & (data["tn"][use]<yy+CELL_M)
                              & data["tree_on_land"][use])
                    if int(inside.sum()) != cell["trees"]:
                        raise ValueError(f"{r['place']} {cell['id']} tree count mismatch")
                    delta = 0. if abs(cell["temperature_delta"]) < .05 else cell["temperature_delta"]
                    center = ((xx+CELL_M/2)/1000, (yy+CELL_M/2)/1000)
                    delta_color = "#568163" if delta < 0 else "#B2605C" if delta > 0 else muted
                    for label, offset, color in (
                        (f"{cell['tree_density']/1000:.1f}k/km²", 4.8, ink),
                        (f"{delta:+.1f}°C", -4.8, delta_color),
                    ):
                        a.annotate(label, center, xytext=(0, offset), textcoords="offset points",
                                   ha="center", va="center", fontsize=7.2, color=color, zorder=8,
                                   path_effects=[effects.withStroke(linewidth=2.7, foreground=bg)])
                else:
                    excluded = all_cells.get((xx, yy))
                    if excluded is not None and excluded["land_km2"] <= 0:
                        continue
                    reason = ("Outside study" if excluded is None else
                              "Limited heat" if excluded["heat_coverage"] >= .9 else "Heat gaps")
                    a.text((xx+CELL_M/2)/1000, (yy+CELL_M/2)/1000, reason, ha="center", va="center",
                           fontsize=6.5, color=muted, zorder=8,
                           path_effects=[effects.withStroke(linewidth=1.5, foreground=bg)])
        fig.text(left, bottom+height+.006, f"{r['letter']}  {r['place']}", fontsize=11, color=r["color"])
        fig.text(left, bottom-.009,
                 f"Mean {r['baseline_temperature']:.1f}°C · {r['n']} cells · {r['baseline_valid_land_fraction']:.0%} of view land has heat",
                 fontsize=7.8, color=muted, va="center")
        def score(value):
            return "n/a" if value is None else f"{value:+.2f}"
        caption = f"Tree–heat correlation {score(r['tree_heat_association']['spearman'])}"
        if args.water_context:
            caption += f" · Water–heat {score(r['water_heat_association']['spearman'])}"
        if r["n"] < 6:
            caption = f"{r['n']} cells · Too few for a local correlation score"
        mini = fig.add_axes((left+.020, bottom-.056, width-.024, .038), facecolor=bg)
        mini.text(.98, .96, caption, transform=mini.transAxes, ha="right", va="top",
                  fontsize=7.6, color=ink, zorder=10,
                  path_effects=[effects.withStroke(linewidth=2.5, foreground=bg)])
        dx = np.array([c["temperature_delta"] for c in r["cells"]])
        dy = np.array([c["tree_density"] for c in r["cells"]])
        wc = np.array([c["nearby_water_percent"] for c in r["cells"]])
        colors = ({"c": wc, "cmap": water_cmap, "norm": water_norm}
                  if args.water_context else {"color": "#527B58"})
        mini.scatter(dx, dy, s=21, **colors, edgecolor=bg, lw=.4, zorder=5)
        mini.axvline(0, color=muted, lw=.65, ls=(0, (2, 3)), alpha=.65)
        mini.set(xlim=(-6.5, 6.5), ylim=(0, density_max*1.2), xticks=[-6, -3, 0, 3, 6], yticks=[0, density_max/2, density_max])
        mini.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:+.0f}°" if v else "0°"))
        mini.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v/1000:g}k" if v else "0"))
        mini.tick_params(colors=muted, labelsize=6.5, length=2, pad=2)
        mini.spines[["top", "right"]].set_visible(False)
        for edge in ("left", "bottom"):
            mini.spines[edge].set_color(muted)
            mini.spines[edge].set_linewidth(.5)
        mini.grid(alpha=.12)
    pair = result["correlations"]
    fig.text(.08, .050, f"Metro: all {len(rows)} qualifying squares · Tree–heat correlation {pair['spearman']:+.2f} (Spearman) · {pair['pearson']:+.2f} (Pearson)", fontsize=8, color=ink)
    fig.text(.96, .050, "arborary.world", ha="right", fontsize=9, color=ink)
    detail = ("Nearby water = water fraction within 500 m of the square" if args.water_context
              else "Grid labels and plots use mapped trees/km² of land")
    fig.text(.52, .040, "Δ°C = square temperature − mean available land temperature in its view · " + detail, ha="center", fontsize=8, color=muted)
    fig.text(.52, .030, "Local correlations: Spearman; n/a for <6 cells or no variation · Small, overlapping samples · Unadjusted associations, not causal effects", ha="center", fontsize=8, color=muted)
    fig.text(.52, .020, "≥90% common heat coverage of land per measured square · Trees incomplete · Clear-sky surface heat, not air temperature or an all-weather summer mean", ha="center", fontsize=7.8, color=muted)
    fig.text(.52, .010, "Sources: USGS Landsat, five summer 2025 dates · Nov 2026 trees: inventories, © OpenStreetMap, USDA National Agriculture Imagery Program · Water: MassGIS", ha="center", fontsize=7.8, color=muted)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.output, dpi=args.dpi, facecolor=bg, bbox_inches="tight", pad_inches=.2)
    plt.close(fig)
    result["render"] = {"overview_markers": "Snapped 2.5 km viewport footprints, no dots",
        "chart_connections": "Overview boxes connect to the central heat list; no connections between scatter points; metro LOWESS retained",
        "region_assignment": "Nearest center among fully covering landmark views; alphabetical place tie-break; gray outside views; each cell appears once",
        "region_owners": owners,
        "headline_sample": f"All {len(rows)} eligible global cells; no water-share cutoff; common land-heat coverage checks retained",
        "upper_axis": {"break_at": 8000, "n": upper_n, "max": high_max, "compressed": True},
        "local_chart_axes": {"x": "Temperature delta, C", "xlim": [-6.5, 6.5], "y": "Trees per km2 land", "ylim": [0, density_max*1.2]},
        "cell_labels": "Mapped tree density with explicit k/km² unit, plus temperature delta with °C unit",
        "water_context_shown": args.water_context,
        "local_chart_color": "Nearby water percent, 0-15 scale; above 15 saturated" if args.water_context else "Constant forest green",
        "heat_map_scale_c": [-6, 6], "heat_palette": "Pale blue (cooler), cream (mean), pale red (warmer)",
        "delta_font_colors": {"cooler": "#568163", "warmer": "#B2605C", "zero": muted},
        "excluded_cell_labels": "Heat gaps, Limited heat, or Outside study; no water-share exclusion labels; pure-water cells unlabelled",
        "local_score_position": "Overlaid inside mini-plot as title",
        "baseline": "Mean", "local_scores": "Spearman"}
    save_json(args.output.with_suffix(".json"), result)
    print("Saved", args.output, flush=True)


if __name__ == "__main__":
    main()
