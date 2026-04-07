#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = ["duckdb", "matplotlib", "numpy", "scipy", "pillow"]
# ///
"""Render a 'night city' dot map of the top N genera in a city.

All trees drawn as a dim structural base; top genera highlighted with
density-based glow, visual hierarchy, vignette, and styled legend.

Usage:
    uv run scripts/render_top_genus_map.py                    # London, top 3
    uv run scripts/render_top_genus_map.py --city USSFO -n 5  # SF, top 5
    uv run scripts/render_top_genus_map.py --city FRPAR -n 4 --title "Paris canopy"
"""
from __future__ import annotations

import argparse

import duckdb
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.offsetbox import AnchoredText
from matplotlib.patches import FancyBboxPatch
import matplotlib.patheffects as pe
import numpy as np
from scipy.ndimage import gaussian_filter

REMOTE_BASE = "https://storage.googleapis.com/trilogy_public_models/duckdb/trees"
DATA_VERSION = 2

BG_COLOR = "#07110C"  # green-black — lifted off pure black

# Muted, related-hue palette — ordered by intended dominance.
# First color is warmest/most saturated (reserved for #1 genus).
PALETTE = [
    "#d4896a",  # warm coral-ochre  (dominant)
    "#8b7db8",  # muted violet
    "#5f9eaa",  # steel teal
    "#b8a44c",  # ochre-gold
    "#6a9a7e",  # jade
]

BASE_TREE_COLOR = "#1a2e22"  # dim gray-green for the structural base layer


def parquet_url(city: str) -> str:
    return f"{REMOTE_BASE}/{city.lower()}_tree_info_v{DATA_VERSION}.parquet"


def compute_density_grid(
    x: np.ndarray, y: np.ndarray, img_w: int, img_h: int,
    x_range: tuple[float, float], y_range: tuple[float, float],
    sigma: float = 8.0,
) -> np.ndarray:
    """Bin points into a 2D grid and smooth with gaussian for density estimation."""
    grid = np.zeros((img_h, img_w), dtype=np.float32)
    # Map coords to pixel indices
    xi = ((x - x_range[0]) / (x_range[1] - x_range[0]) * (img_w - 1)).astype(np.int32)
    yi = ((y - y_range[0]) / (y_range[1] - y_range[0]) * (img_h - 1)).astype(np.int32)
    xi = np.clip(xi, 0, img_w - 1)
    yi = np.clip(yi, 0, img_h - 1)
    np.add.at(grid, (yi, xi), 1)
    return gaussian_filter(grid, sigma=sigma)


def lookup_density(
    x: np.ndarray, y: np.ndarray, density_grid: np.ndarray,
    x_range: tuple[float, float], y_range: tuple[float, float],
) -> np.ndarray:
    """Look up per-point density from a pre-computed grid."""
    img_h, img_w = density_grid.shape
    xi = ((x - x_range[0]) / (x_range[1] - x_range[0]) * (img_w - 1)).astype(np.int32)
    yi = ((y - y_range[0]) / (y_range[1] - y_range[0]) * (img_h - 1)).astype(np.int32)
    xi = np.clip(xi, 0, img_w - 1)
    yi = np.clip(yi, 0, img_h - 1)
    return density_grid[yi, xi]


def hex_to_rgb(h: str) -> tuple[float, float, float]:
    h = h.lstrip("#")
    return tuple(int(h[i:i+2], 16) / 255.0 for i in (0, 2, 4))


def main() -> None:
    parser = argparse.ArgumentParser(description="Render top-N genus dot map")
    parser.add_argument("--city", default="GBLON", help="City code (default: GBLON)")
    parser.add_argument("-n", "--top-n", type=int, default=3, help="Number of highlighted genera (default: 3)")
    parser.add_argument("--dpi", type=int, default=400, help="Output DPI (default: 400)")
    parser.add_argument("--width", type=float, default=20, help="Figure width inches (height auto-derived from geo aspect)")
    parser.add_argument("--output", default=None, help="Output path")
    parser.add_argument("--no-legend", action="store_true")
    parser.add_argument("--no-title", action="store_true")
    parser.add_argument("--title", default=None, help="Custom title text")
    parser.add_argument("--no-vignette", action="store_true")
    parser.add_argument("--bg", default=BG_COLOR, help=f"Background color (default: {BG_COLOR})")
    parser.add_argument("--glow-sigma", type=float, default=3.0, help="Glow blur radius (default: 3.0)")
    parser.add_argument("--base-dot", type=float, default=0.08, help="Base layer dot size (default: 0.08)")
    parser.add_argument("--highlight-dot", type=float, default=0.25, help="Highlight dot size (default: 0.25)")
    args = parser.parse_args()

    url = parquet_url(args.city)
    palette = PALETTE[: args.top_n]
    if len(palette) < args.top_n:
        raise SystemExit(f"Need at least {args.top_n} palette entries, have {len(PALETTE)}")

    con = duckdb.connect(":memory:")
    con.execute("INSTALL httpfs; LOAD httpfs;")

    print(f"Fetching {url} ...")

    # ── Get ALL tree points + genus ──
    all_points = con.execute(f"""
        SELECT split_part(species, ' ', 1) AS genus, latitude, longitude
        FROM '{url}'
        WHERE latitude IS NOT NULL AND longitude IS NOT NULL
    """).fetchnumpy()

    all_genus = all_points["genus"]
    all_lat = all_points["latitude"].astype(np.float64)
    all_lon = all_points["longitude"].astype(np.float64)
    total = len(all_lat)
    print(f"Total trees: {total:,}")

    # ── Top N genera ──
    top_genera = con.execute(f"""
        SELECT split_part(species, ' ', 1) AS genus, count(*) AS cnt
        FROM '{url}'
        WHERE latitude IS NOT NULL AND longitude IS NOT NULL
          AND species IS NOT NULL AND species != ''
        GROUP BY genus
        ORDER BY cnt DESC
        LIMIT {args.top_n}
    """).fetchall()

    genus_names = [r[0] for r in top_genera]
    genus_counts = {r[0]: r[1] for r in top_genera}
    print(f"Highlighted: {', '.join(f'{g} ({genus_counts[g]:,})' for g in genus_names)}")

    # ── Top species per genus (for legend annotation) ──
    genus_list_sql = ", ".join(f"'{g}'" for g in genus_names)
    top_species_rows = con.execute(f"""
        WITH ranked AS (
            SELECT species, split_part(species, ' ', 1) AS genus,
                   count(*) AS cnt,
                   row_number() OVER (PARTITION BY split_part(species, ' ', 1) ORDER BY count(*) DESC) AS rn
            FROM '{url}'
            WHERE species IS NOT NULL AND species != ''
              AND split_part(species, ' ', 1) IN ({genus_list_sql})
            GROUP BY species, split_part(species, ' ', 1)
        )
        SELECT genus, species FROM ranked WHERE rn = 1
    """).fetchall()
    genus_top_species = {r[0]: r[1] for r in top_species_rows}

    # ── Bounding box (with padding) ──
    pad_frac = 0.02
    lon_min, lon_max = float(all_lon.min()), float(all_lon.max())
    lat_min, lat_max = float(all_lat.min()), float(all_lat.max())
    lon_pad = (lon_max - lon_min) * pad_frac
    lat_pad = (lat_max - lat_min) * pad_frac
    lon_range = (lon_min - lon_pad, lon_max + lon_pad)
    lat_range = (lat_min - lat_pad, lat_max + lat_pad)

    # ── Density grid (for glow + per-point scaling) ──
    density_res = 800
    aspect = (lon_range[1] - lon_range[0]) / (lat_range[1] - lat_range[0])
    grid_w = int(density_res * max(aspect, 1.0))
    grid_h = int(density_res / min(aspect, 1.0))

    overall_density = compute_density_grid(
        all_lon, all_lat, grid_w, grid_h, lon_range, lat_range, sigma=12.0,
    )

    # ── Build figure ──
    # Correct aspect ratio for latitude — 1° lon is cos(lat) × 1° lat
    mid_lat = (lat_range[0] + lat_range[1]) / 2
    cos_correction = np.cos(np.radians(mid_lat))

    # Size figure to match data proportions
    data_width = (lon_range[1] - lon_range[0]) * cos_correction
    data_height = lat_range[1] - lat_range[0]
    data_aspect = data_width / data_height
    fig_w = args.width
    fig_h = fig_w / data_aspect

    fig, ax = plt.subplots(figsize=(fig_w, fig_h), facecolor=args.bg)
    ax.set_facecolor(args.bg)
    ax.set_aspect(1.0 / cos_correction)
    ax.axis("off")
    ax.set_xlim(*lon_range)
    ax.set_ylim(*lat_range)

    # ── Layer 1: structural base (all trees, dim) ──
    print("Drawing base layer ...")
    base_density = lookup_density(all_lon, all_lat, overall_density, lon_range, lat_range)
    base_density_norm = np.clip(base_density / (np.percentile(base_density, 95) + 1e-9), 0, 1)
    base_alpha = 0.12 + 0.25 * base_density_norm  # denser = slightly brighter
    base_sizes = args.base_dot * (0.8 + 0.6 * base_density_norm)

    # Scatter with per-point alpha via RGBA
    br, bg_c, bb = hex_to_rgb(BASE_TREE_COLOR)
    base_colors = np.column_stack([
        np.full(total, br), np.full(total, bg_c), np.full(total, bb), base_alpha,
    ])
    ax.scatter(all_lon, all_lat, s=base_sizes, c=base_colors, linewidths=0, rasterized=True)

    # ── Layer 2: glow underneath highlighted genera ──
    print("Drawing genus glow layers ...")
    for i, genus in enumerate(genus_names):
        mask = all_genus == genus
        gx, gy = all_lon[mask], all_lat[mask]
        genus_density = compute_density_grid(
            gx, gy, grid_w, grid_h, lon_range, lat_range, sigma=args.glow_sigma * 4,
        )
        # Render glow as imshow underneath
        r, g, b = hex_to_rgb(palette[i])
        glow_norm = genus_density / (genus_density.max() + 1e-9)
        glow_alpha = glow_norm * 0.35  # subtle
        glow_rgba = np.zeros((grid_h, grid_w, 4))
        glow_rgba[..., 0] = r
        glow_rgba[..., 1] = g
        glow_rgba[..., 2] = b
        glow_rgba[..., 3] = glow_alpha
        ax.imshow(
            glow_rgba, extent=[*lon_range, *lat_range],
            origin="lower", aspect="auto", interpolation="bilinear", zorder=2 + i,
        )

    # ── Layer 3: crisp highlighted dots ──
    print("Drawing highlighted genera ...")
    for i, genus in enumerate(reversed(genus_names)):
        # Draw least common first, most common on top
        idx = len(genus_names) - 1 - i
        mask = all_genus == genus
        gx, gy = all_lon[mask], all_lat[mask]

        pt_density = lookup_density(gx, gy, overall_density, lon_range, lat_range)
        pt_density_norm = np.clip(pt_density / (np.percentile(pt_density, 90) + 1e-9), 0, 1)

        # Density-scaled size and alpha
        sizes = args.highlight_dot * (0.7 + 1.0 * pt_density_norm)
        alphas = 0.5 + 0.4 * pt_density_norm  # 0.5–0.9

        r, g, b = hex_to_rgb(palette[idx])
        colors = np.column_stack([
            np.full(len(gx), r), np.full(len(gx), g), np.full(len(gx), b), alphas,
        ])
        ax.scatter(
            gx, gy, s=sizes, c=colors, linewidths=0, rasterized=True,
            zorder=10 + idx,
        )

    # ── Vignette ──
    if not args.no_vignette:
        print("Applying vignette ...")
        vig_h, vig_w = 600, 600
        yy, xx = np.mgrid[:vig_h, :vig_w]
        cx, cy = vig_w / 2, vig_h / 2
        dist = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
        dist_norm = dist / (np.sqrt(cx**2 + cy**2))
        # Smooth ramp: transparent center, dark edges
        vig_alpha = np.clip((dist_norm - 0.3) / 0.7, 0, 1) ** 1.8 * 0.65
        br, bg_c, bb = hex_to_rgb(args.bg)
        vig_rgba = np.zeros((vig_h, vig_w, 4))
        vig_rgba[..., 0] = br
        vig_rgba[..., 1] = bg_c
        vig_rgba[..., 2] = bb
        vig_rgba[..., 3] = vig_alpha
        ax.imshow(
            vig_rgba, extent=[*lon_range, *lat_range],
            origin="lower", aspect="auto", interpolation="bilinear", zorder=50,
        )

    # ── Legend ──
    if not args.no_legend:
        for i, genus in enumerate(genus_names):
            cnt = genus_counts[genus]
            example = genus_top_species.get(genus, "")
            ex_label = f"\n     e.g. {example}" if example else ""
            ax.scatter(
                [], [], s=60, c=palette[i], linewidths=0,
                label=f"  {genus}   {cnt:,}{ex_label}", zorder=100,
            )

        legend = ax.legend(
            loc="lower right",
            fontsize=11,
            frameon=True,
            facecolor=args.bg,
            edgecolor="none",
            framealpha=0.75,
            markerscale=1.8,
            labelcolor="#b8b8b8",
            borderpad=1.0,
            labelspacing=0.8,
            handletextpad=0.8,
        )
        legend.set_zorder(100)
        for text in legend.get_texts():
            text.set_fontstyle("italic")
            text.set_fontfamily("serif")

    # ── Title ──
    if not args.no_title:
        city_name = args.city
        # Try to get a nice name
        CITY_NAMES = {
            "GBLON": "London", "USSFO": "San Francisco", "USNYC": "New York City",
            "USBOS": "Boston", "FRPAR": "Paris", "USBTV": "Burlington",
            "CAVAN": "Vancouver", "DEBER": "Berlin", "NLAMS": "Amsterdam",
            "AUMEL": "Melbourne",
        }
        city_label = CITY_NAMES.get(args.city, args.city)
        title_text = args.title or f"The urban canopy of {city_label}, by genus"
        ax.text(
            0.5, 0.97, title_text,
            transform=ax.transAxes, ha="center", va="top",
            fontsize=18, fontfamily="serif", fontstyle="italic",
            color="#8a9a8a",
            path_effects=[pe.withStroke(linewidth=3, foreground=args.bg)],
            zorder=100,
        )

    plt.subplots_adjust(left=0, right=1, top=1, bottom=0)

    out_path = args.output or f"{args.city.lower()}_top{args.top_n}_genus.png"
    fig.savefig(out_path, dpi=args.dpi, facecolor=args.bg, bbox_inches="tight", pad_inches=0.1)
    plt.close(fig)
    print(f"Saved → {out_path}")


if __name__ == "__main__":
    main()
# uv run scripts/render_top_genus_map.py                        # London, top 3 (default)
# uv run scripts/render_top_genus_map.py --city USSFO -n 5      # SF, top 5
# uv run scripts/render_top_genus_map.py --city FRPAR -n 4 --title "Paris canopy"
