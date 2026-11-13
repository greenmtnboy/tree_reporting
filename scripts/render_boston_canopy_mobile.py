#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = ["duckdb", "matplotlib", "numpy", "requests", "scipy", "statsmodels", "shapely>=2.1", "rasterio", "pyproj", "pillow"]
# ///
"""Mobile companion: the same metro scatter and four large, linked tree/heat views."""
import argparse
import copy
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt
import matplotlib.patheffects as effects
from matplotlib.lines import Line2D
from matplotlib.patches import Rectangle
from PIL import Image

from render_boston_canopy_grid import (
    BG, INK, MUTED, HEAT, NORM, OUT, CELL_M,
    draw_headline_axis, inset_water, local_map, prepare_poster, score,
)
from boston_income_heat_data import save_json


def select_mobile_views(views):
    """Four evenly spaced temperature ranks, without choosing on correlation."""
    if len(views) < 4:
        raise ValueError('At least four audited views are required')
    ordered = sorted(views, key=lambda v: (v['baseline_temperature'], v['place']))
    indexes = np.rint(np.linspace(0, len(ordered)-1, 4)).astype(int)
    selected = copy.deepcopy([ordered[i] for i in indexes])
    for i, view in enumerate(selected):
        view['desktop_letter'] = view['letter']
        view['letter'] = chr(65+i)
        cells = view['paired_cells']
        values = np.array([[c['temperature'], c['canopy_percent']] for c in cells])
        median = np.median(values, axis=0)
        spread = np.maximum(np.subtract(*np.percentile(values, [75, 25], axis=0)), .01)
        distances = np.sum(((values-median)/spread)**2, axis=1)
        chosen = min(range(len(cells)), key=lambda j: (distances[j], cells[j]['id']))
        view['callout_cell_id'] = cells[chosen]['id']
    return selected, {
        'view_rule': 'Four evenly spaced ranks of mean temperature among the twelve desktop views (coolest to hottest); no selection by correlation strength or trend residual.',
        'temperature_ranks_one_based': (indexes+1).tolist(),
        'cell_rule': 'For each view, highlight the actual qualifying cell nearest its median temperature and median canopy, scaled by each within-view interquartile range. No averaging or synthetic scatter points.',
        'interpretation': 'Illustrative range of the audited views, not a representative random sample of Boston.',
    }


def render_mobile(result, data, rows, desktop_views, output, dpi, layout='grid'):
    views, selection = select_mobile_views(desktop_views)
    stacked = layout == 'stacked'
    fig = plt.figure(figsize=(8.5, 43) if stacked else (12, 22.8), facecolor=BG)
    fw, fh = fig.get_size_inches()
    def text(x, inch_y, value, size=17, **kwargs):
        return fig.text(x, inch_y/fh, value, fontsize=size, color=kwargs.pop('color', INK), **kwargs)
    text(.5, 42.15 if stacked else 22.08, 'Summertime Shadeness', 33 if stacked else 40, ha='center')
    text(.5, 41.55 if stacked else 21.55, 'Do trees cut the Boston summer heat?', 20 if stacked else 23, ha='center')
    text(.5, 41.13 if stacked else 21.15, 'Correlational, not causal.', 19 if stacked else 20, ha='center', color=MUTED)

    ax = fig.add_axes((.15, 35.8/fh, .72, 4.4/fh) if stacked else (.16, 17.05/fh, .68, 3.45/fh))
    owners = draw_headline_axis(ax, rows, views, point_size=30, tick_size=18)
    ax.set_xlabel('Summer surface heat · °C', fontsize=20, color=INK, labelpad=9)
    text(.15 if stacked else .16, 40.45 if stacked else 20.72, 'Estimated canopy · % of land', 19)
    rho = result['canopy_comparison']['associations']['canopy_percent']['spearman']
    ax.text(.97, .96, f'Correlation {score(rho)}', transform=ax.transAxes, ha='right', va='top',
            fontsize=23, color=INK, path_effects=[effects.withStroke(linewidth=3, foreground=BG)])
    text(.51, 34.92 if stacked else 16.17, f'{len(rows)} squares · 500 m each · Spearman correlation', 16, ha='center', color=MUTED)
    text(.5, 34.53 if stacked else 15.79, 'Outlined map cell = highlighted scatter dot', 18, ha='center')
    water = inset_water(data)
    cells = {(c['west'], c['south']): c for c in result['cells']}
    by_id = {c['id']: c for c in rows}

    # A real cell rendered through the same map/label code as both poster targets.
    legend_view = copy.deepcopy(views[1])
    legend_cell = by_id[legend_view['callout_cell_id']]
    w, s = legend_cell['west'], legend_cell['south']
    legend_view['bounds_utm'] = [w, s, w+CELL_M, s+CELL_M]
    legend_left, legend_bottom, legend_width = (.14, 32.5, .18) if stacked else (.065, 14.02, .115)
    local_map(fig, legend_view, data, cells, water, legend_left, legend_bottom/fh, legend_width, legend_width*fw/fh,
              False, True, captions=False,
              typography={'label_size': 23, 'label_offset': 14, 'dot_size': .8})
    legend_x = .36 if stacked else .205
    text(legend_x, 33.85 if stacked else 15.19, '% = estimated crown coverage', 19)
    text(legend_x, 33.49 if stacked else 14.84, 'Green above / red below map median', 15.5, color=MUTED)
    text(legend_x, 33.08 if stacked else 14.41, 'Δ°C = heat vs map mean', 19)
    text(legend_x, 32.72 if stacked else 14.06, 'Green cooler / red warmer', 16, color=MUTED)
    text(.36 if stacked else .66, 32.30 if stacked else 15.19, 'Charcoal dots = trees', 19)
    cax = fig.add_axes((.14, 31.9/fh, .28, .07/fh) if stacked else (.66, 14.34/fh, .26, .07/fh))
    cb = fig.colorbar(plt.cm.ScalarMappable(norm=NORM, cmap=HEAT), cax=cax,
                      orientation='horizontal', ticks=[-6, 0, 6], extend='both')
    cb.outline.set_visible(False)
    cb.ax.tick_params(labelsize=16, colors=MUTED, length=2, pad=3)
    text(.48 if stacked else .66, 31.94 if stacked else 14.69, 'Background: cooler → warmer', 16)
    text(.48 if stacked else .66, 31.59 if stacked else 13.80, 'Relative to each map’s mean', 16, color=MUTED)

    map_width = .74 if stacked else .42
    map_inches = map_width*fw
    routes = []
    for i, view in enumerate(views):
        # Cool views in the left column, warmer views in the right: leaders
        # can leave the corresponding chart edge without crossing each other.
        map_left = .13 if stacked else (.055 if i < 2 else .525)
        top_inch = 30.75-i*(map_inches+1.1) if stacked else 13.10-(i % 2)*6.2
        bottom = (top_inch-map_inches)/fh
        a = local_map(fig, view, data, cells, water, map_left, bottom, map_width, map_inches/fh,
                      False, True, captions=False,
                      typography={'label_size': 21.5 if stacked else 24, 'gap_size': 16, 'water_size': 16,
                                  'label_offset': 13.5 if stacked else 15, 'dot_size': .8})
        text(map_left, top_inch+.23, f"{view['letter']}  {view['place']}", 26, color=view['color'])
        text(map_left, top_inch-map_inches-.34,
             f"{view['baseline_temperature']:.1f}°C · {view['baseline_valid_land_fraction']:.0%} heat coverage · 2 × 2 km", 18 if stacked else 17, color=MUTED)
        a.add_patch(Rectangle((0, 0), 1, 1, transform=a.transAxes, fill=False,
                             edgecolor=view['color'], linewidth=1.2, zorder=9, clip_on=False))
        cell = by_id[view['callout_cell_id']]
        a.add_patch(Rectangle((cell['west']/1000, cell['south']/1000), .5, .5, fill=False,
                             edgecolor=view['color'], linewidth=2.5, zorder=10,
                             path_effects=[effects.withStroke(linewidth=4.5, foreground=BG)]))
        ax.scatter([cell['temperature']], [cell['canopy_percent']], s=135,
                   color=view['color'], edgecolors=BG, linewidths=1.8, zorder=8)
        endpoint_x = map_left if i < 2 else map_left+map_width
        routes.append((view, cell, (top_inch-.32)/fh, endpoint_x))

    fig.canvas.draw()
    for i, (view, cell, endpoint_y, endpoint_x) in enumerate(routes):
        px, py = fig.transFigure.inverted().transform(ax.transData.transform((cell['temperature'], cell['canopy_percent'])))
        left_side = i < 2
        rail = ((.035, .070, .930, .965) if stacked else (.025, .040, .960, .975))[i]
        exit_y = py-.004
        line = Line2D([px, rail, rail, endpoint_x], [py, exit_y, endpoint_y, endpoint_y],
                      transform=fig.transFigure, color=view['color'], linewidth=1.05,
                      alpha=.70, zorder=4, solid_capstyle='round',
                      path_effects=[effects.withStroke(linewidth=2.5, foreground=BG)])
        fig.add_artist(line)

    days = len(result['provenance']['dates'])
    text(.5, 1.57 if stacked else 1.05, f'{days} clear-sky days · Summer 2025 · Surface Heat (Not air)', 17, ha='center')
    if stacked:
        text(.5, 1.20, 'Estimated crowns, overlaps merged · >50% water excluded', 16, ha='center', color=MUTED)
        text(.5, .85, 'Tiles selected for available heat coverage; tree data incomplete.', 15, ha='center', color=MUTED)
        text(.5, .48, 'Heat: USGS Landsat · Water: USGS / MassDEP / MassGIS', 15, ha='center', color=MUTED)
        text(.5, .16, 'Trees: inventories, © OSM, USDA NAIP · arborary.world', 15, ha='center', color=MUTED)
    else:
        text(.5, .72, 'Estimated crowns, overlaps merged · >50% water excluded · Tree data incomplete', 16, ha='center', color=MUTED)
        text(.5, .40, 'Tiles selected for available heat coverage · Heat: USGS Landsat · Water: USGS / MassDEP / MassGIS', 15, ha='center', color=MUTED)
        text(.5, .10, 'Trees: inventories, © OpenStreetMap, USDA NAIP · arborary.world', 15, ha='center', color=MUTED)

    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    text_boxes = [(t.get_text(), t.get_window_extent(renderer)) for t in fig.texts]
    for label, box in text_boxes:
        if box.x0 < 0 or box.x1 > fig.bbox.width or box.y0 < 0 or box.y1 > fig.bbox.height:
            raise ValueError(f'Mobile text escapes page: {label}')
    for i, (label, box) in enumerate(text_boxes):
        for other, other_box in text_boxes[i+1:]:
            if box.overlaps(other_box):
                raise ValueError(f'Mobile labels overlap: {label} / {other}')

    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=dpi, facecolor=BG)
    plt.close(fig)
    with Image.open(output) as image:
        output_pixels = image.size
        image.thumbnail((1100, 7000))
        image.save(output.with_name(output.stem+'_preview.png'))
    report = copy.deepcopy(result)
    report['mobile'] = {'selection': selection, 'views': views, 'global_region_owners': owners,
                        'output': str(output), 'output_pixels': output_pixels, 'dpi': dpi,
                        'layout': layout, 'description': 'One metro scatter, four linked maps, real-cell leaders, legend and guidance',
                        'typography_pt': {'map_labels': 21.5 if stacked else 24, 'map_titles': 26, 'chart_ticks': 18, 'captions': 18 if stacked else 17},
                        'global_sample_unchanged': True, 'desktop_output_unchanged': True,
                        'callouts': [{'place': v['place'], 'cell_id': v['callout_cell_id'],
                                     'temperature': by_id[v['callout_cell_id']]['temperature'],
                                     'canopy_percent': by_id[v['callout_cell_id']]['canopy_percent']}
                                    for v in views]}
    save_json(output.with_suffix('.json'), report)
    print('Mobile views:', [(v['place'], v['callout_cell_id']) for v in views], flush=True)
    print('Saved', output, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dpi', type=int, default=240)
    parser.add_argument('--output-dir', type=Path, default=OUT)
    parser.add_argument('--layout', choices=('grid', 'stacked'), default='grid')
    args = parser.parse_args()
    result, data, rows, views = prepare_poster('disjoint')
    name = 'boston_canopy_heat_mobile.png' if args.layout == 'grid' else 'boston_canopy_heat_mobile_stacked.png'
    render_mobile(result, data, rows, views, args.output_dir/name, args.dpi, args.layout)


if __name__ == '__main__':
    main()
