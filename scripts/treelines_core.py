"""Shared palettes and geographic drawing primitives for Treelines posters.

Coordinates passed to drawing functions are projected kilometres. These
functions are also imported by the established Boston renderer.
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.patheffects as path_effects
from matplotlib.colors import to_rgb
from matplotlib.patches import PathPatch, Rectangle, Circle, Polygon
from matplotlib.collections import PatchCollection
import numpy as np
from scipy.ndimage import gaussian_filter

BG = "#090f14"

AMBER = "#ffc681"

CYAN = "#60c8d7"

TEXT = "#d9e1e3"

MUTED = "#81949b"

PALETTES = {
    "night": {"bg": BG, "records": AMBER, "aerial": CYAN, "text": TEXT,
              "muted": MUTED, "water": "#132430", "water_edge": "#1a303c"},
    "forest": {"bg": "#08120e", "records": "#ced493", "aerial": "#66b68b",
               "text": "#dce5d6", "muted": "#8b9e8d", "water": "#12251f",
               "water_edge": "#1a3027"},
    "light": {"bg": "#f7f5ed", "records": "#8b651e", "aerial": "#216548",
              "text": "#233b30", "muted": "#68746a", "water": "#dbe8e9",
              "water_edge": "#cedfe1", "subtitle_trees": "#216548",
              "subtitle_water": "#397888", "map_label": "#28533f",
              "map_label_outline": "#ffffff"},
}

def draw_circle_fade(ax, bounds, circle, background=BG):
    xmin, xmax, ymin, ymax = bounds
    width = 1400
    height = max(1, round(width * (ymax - ymin) / (xmax - xmin)))
    xx = np.linspace(xmin, xmax, width)[None, :]
    yy = np.linspace(ymin, ymax, height)[:, None]
    cx, cy = circle["centre_projected_km"]
    distance = np.hypot(xx - cx, yy - cy)
    t = np.clip((distance - circle["fade_start_km"]) /
                (circle["fade_end_km"] - circle["fade_start_km"]), 0, 1)
    rgba = np.empty((height, width, 4), dtype=np.float32)
    rgba[..., :3] = to_rgb(background)
    rgba[..., 3] = t * t * (3 - 2 * t)
    # Overlay all map layers, leaving figure-level titles/legend untouched.
    ax.imshow(rgba, extent=bounds, origin="lower", interpolation="bilinear", zorder=5)

def draw_map(ax, x, y, sources, bounds, layers, dot_size, glow, water_paths=(), circle=None, palette=None, canopy_widths=None):
    palette = palette or PALETTES["night"]
    background = palette["bg"]
    ax.set_facecolor(background)
    xmin, xmax, ymin, ymax = bounds
    ax.set_xlim(xmin, xmax)
    ax.set_ylim(ymin, ymax)
    ax.axis("off")
    for path in water_paths:
        ax.add_patch(PathPatch(path, facecolor=palette["water"], edgecolor=palette["water_edge"], lw=.15, zorder=0))
    if water_paths:
        # Let the water fade into the poster at the frame, avoiding a hard rectangle.
        yy, xx = np.mgrid[0:1:500j, 0:1:500j]
        edge_distance = np.minimum.reduce([xx, 1-xx, yy, 1-yy])
        fade = np.empty((500, 500, 4))
        fade[..., :3] = to_rgb(background)
        fade[..., 3] = 1 - np.clip(edge_distance / .08, 0, 1)
        ax.imshow(fade, origin="lower", extent=bounds, interpolation="bilinear", zorder=.5)
    for names, color, alpha in layers:
        mask = np.isin(sources, names)
        px, py = x[mask], y[mask]
        if not len(px):
            continue
        if glow:
            w = 1300
            h = max(1, round(w * (ymax - ymin) / (xmax - xmin)))
            counts, _, _ = np.histogram2d(py, px, bins=(h, w), range=((ymin, ymax), (xmin, xmax)))
            density = gaussian_filter(counts.astype(np.float32), sigma=2.4)
            norm = max(float(np.percentile(density[density > 0], 99.5)), 1e-6)
            rgba = np.empty((h, w, 4), dtype=np.float32)
            rgba[..., :3] = to_rgb(color)
            rgba[..., 3] = np.clip(density / norm, 0, 1) ** .8 * glow
            ax.imshow(rgba, origin="lower", extent=bounds, interpolation="bilinear", zorder=1)
        if canopy_widths is not None:
            widths = canopy_widths[mask]
            valid = np.isfinite(widths) & (widths > 0)
            crowns = [Circle((cx, cy), diameter / 2000)
                      for cx, cy, diameter in zip(px[valid], py[valid], widths[valid])]
            # Coordinates are kilometres and widths are diameters in metres.
            # Radius = width/2, with no visual enlargement or size clipping.
            rgb = to_rgb(color)
            ax.add_collection(PatchCollection(crowns, facecolor=(*rgb, .12),
                              edgecolor="none", linewidth=0, zorder=1.8, rasterized=True))
            # Use the same path renderer for centers and crowns. Tiny scatter
            # markers use a different Agg rasterization path and can appear
            # displaced by about half a pixel despite identical coordinates.
            center_radius = (.5 * np.sqrt(dot_size) / 72 * ax.figure.dpi
                             * (xmax - xmin) / ax.get_window_extent().width)
            centers = [Circle((cx, cy), center_radius) for cx, cy in zip(px, py)]
            ax.add_collection(PatchCollection(centers, facecolor=(*rgb, alpha),
                              edgecolor="none", linewidth=0, zorder=2, rasterized=True))
        else:
            ax.scatter(px, py, s=dot_size, c=color, alpha=alpha, linewidths=0, zorder=2, rasterized=True)
    if circle is not None:
        draw_circle_fade(ax, bounds, circle, background)
    # Set after images: imshow must not override geographic aspect correction.
    ax.set_aspect("equal", adjustable="box")

def draw_cartographic_details(fig, ax, bounds, palette, scale):
    """Center a north marker and kilometre scale within the map's soft frame."""
    ink = palette.get("map_label", palette["text"])
    halo = [path_effects.withStroke(linewidth=2.2*scale, foreground=palette["bg"])]
    # North is up in this local equirectangular projection. Both ornaments
    # share the main map's horizontal center, not the whole poster's center.
    position = ax.get_position()
    cx, cy = position.x0 + position.width / 2, position.y0 + .89*position.height
    dx, dy = .005, .015
    for vertices, color in (
        ([(cx, cy+dy), (cx-dx, cy-dy*.55), (cx, cy-dy*.2)], ink),
        ([(cx, cy+dy), (cx+dx, cy-dy*.55), (cx, cy-dy*.2)], palette["bg"]),
    ):
        fig.add_artist(Polygon(vertices, transform=fig.transFigure, closed=True,
                               facecolor=color, edgecolor=ink,
                               linewidth=.55*scale, path_effects=halo, zorder=30))
    fig.text(cx, cy+dy+.006, "N", ha="center", va="bottom",
             color=ink, fontsize=9*scale, path_effects=halo, zorder=31)
    # Data units are kilometres: each segment is exactly 1 projected km.
    xmin, xmax, ymin, ymax = bounds
    sx, sy = (xmin+xmax)/2 - 1, ymin + .095*(ymax-ymin)
    for index in range(2):
        ax.add_patch(Rectangle((sx+index, sy), 1, .065,
                               facecolor=ink if index == 0 else palette["bg"],
                               edgecolor=ink, linewidth=.55*scale, path_effects=halo, zorder=20))
    for offset, label in ((0, "0"), (1, "1"), (2, "2 km")):
        ax.text(sx+offset, sy+.19, label, ha="center", va="bottom",
                color=ink, fontsize=7.5*scale, path_effects=halo, zorder=21)

def draw_centered_text_runs(fig, x, y, runs):
    """Keep a mixed-colour/size line centered, with every run on one baseline."""
    renderer = fig.canvas.get_renderer()
    artists = [fig.text(0, y, text, color=color, fontsize=size, fontstyle=style,
                        ha="left", va="baseline")
               for text, color, size, style in runs]
    widths = [artist.get_window_extent(renderer).width / fig.bbox.width for artist in artists]
    left = x - sum(widths) / 2
    for artist, width in zip(artists, widths):
        artist.set_x(left)
        left += width
