#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["duckdb", "matplotlib", "numpy", "requests"]
# ///
"""Dot map with top N genera tinted in nature colors, everything else in gray.

Usage:
    uv run scripts/render_grayscale_baseline.py
    uv run scripts/render_grayscale_baseline.py --city USSFO -n 5
"""
from __future__ import annotations

import argparse

import tempfile
from pathlib import Path

import duckdb
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import numpy as np
import requests


def load_inter_font() -> str:
    """Download Inter from Google Fonts and register it. Returns the font family name."""
    cache_dir = Path(tempfile.gettempdir()) / "inter_font"
    cache_dir.mkdir(exist_ok=True)
    regular = cache_dir / "Inter-Regular.ttf"
    bold = cache_dir / "Inter-Bold.ttf"

    urls = {
        regular: "https://fonts.gstatic.com/s/inter/v18/UcCO3FwrK3iLTeHuS_nVMrMxCp50SjIw2boKoduKmMEVuLyfAZ9hjQ.ttf",
        bold: "https://fonts.gstatic.com/s/inter/v18/UcCO3FwrK3iLTeHuS_nVMrMxCp50SjIw2boKoduKmMEVuFuYAZ9hjQ.ttf",
    }
    for path, url in urls.items():
        if not path.exists():
            print(f"Downloading {path.name} ...")
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
            path.write_bytes(resp.content)
        fm.fontManager.addfont(str(path))

    return "Inter"

REMOTE_BASE = "https://storage.googleapis.com/trilogy_public_models/duckdb/trees"
DATA_VERSION = 2

GENUS_PALETTE = [
    "#E56B6F",  # coral red
    "#D9A441",  # golden amber
    "#2FA27F",  # teal green
    "#5B8BD4",  # steel blue
    "#C47ED0",  # soft violet
    "#E0913A",  # burnt orange
    "#4EC6C1",  # aqua
    "#A8BF54",  # olive lime
    "#D46B8C",  # rose
    "#6DB88F",  # sage
]

BASE_COLOR = "#cccccc"


def main() -> None:
    font_family = load_inter_font()

    parser = argparse.ArgumentParser()
    parser.add_argument("--city", default="GBLON")
    parser.add_argument("-n", "--top-n", type=int, default=10)
    parser.add_argument("--dpi", type=int, default=300)
    parser.add_argument("--width", type=float, default=20)
    parser.add_argument("--dot-size", type=float, default=0.3)
    parser.add_argument("--dot-alpha", type=float, default=0.5)
    parser.add_argument("--bg", default="#0a0d10")
    parser.add_argument("--output", default=None)
    parser.add_argument("--title", default=None, help="Custom title text")
    args = parser.parse_args()

    url = f"{REMOTE_BASE}/{args.city.lower()}_tree_info_v{DATA_VERSION}.parquet"

    con = duckdb.connect(":memory:")
    con.execute("INSTALL httpfs; LOAD httpfs;")

    print(f"Fetching {url} ...")
    data = con.execute(f"""
        SELECT split_part(species, ' ', 1) AS genus, latitude, longitude
        FROM '{url}'
        WHERE latitude IS NOT NULL AND longitude IS NOT NULL
    """).fetchnumpy()

    genus = data["genus"]
    lat = data["latitude"].astype(np.float64)
    lon = data["longitude"].astype(np.float64)
    print(f"Total trees: {len(lat):,}")

    # Find top N genera
    top_genera = con.execute(f"""
        SELECT split_part(species, ' ', 1) AS genus, count(*) AS cnt
        FROM '{url}'
        WHERE latitude IS NOT NULL AND longitude IS NOT NULL
          AND species IS NOT NULL AND species != ''
        GROUP BY genus ORDER BY cnt DESC
        LIMIT {args.top_n}
    """).fetchall()
    genus_names = [r[0] for r in top_genera]
    print(f"Top {args.top_n}: {', '.join(f'{g} ({c:,})' for g, c in top_genera)}")

    # Look up common names from enrichment table
    enrichment_url = f"{REMOTE_BASE}/tree_enrichment_v{DATA_VERSION}.parquet"
    genus_list_sql = ", ".join(f"'{g}'" for g in genus_names)
    common_rows = con.execute(f"""
        WITH ranked AS (
            SELECT split_part(species, ' ', 1) AS genus,
                   common_names[1] AS common_name,
                   row_number() OVER (PARTITION BY split_part(species, ' ', 1)) AS rn
            FROM '{enrichment_url}'
            WHERE split_part(species, ' ', 1) IN ({genus_list_sql})
              AND common_names IS NOT NULL AND len(common_names) > 0
        )
        SELECT genus, common_name FROM ranked WHERE rn = 1
    """).fetchall()
    genus_common = {r[0]: r[1].strip() for r in common_rows}

    # Assign colors: top genera get palette colors, everything else gets gray
    genus_color_map = {g: GENUS_PALETTE[i] for i, g in enumerate(genus_names)}
    colors = np.array([genus_color_map.get(g, BASE_COLOR) for g in genus])

    # Bounding box
    pad = 0.02
    lon_min, lon_max = float(lon.min()), float(lon.max())
    lat_min, lat_max = float(lat.min()), float(lat.max())
    lon_pad = (lon_max - lon_min) * pad
    lat_pad = (lat_max - lat_min) * pad
    lon_range = (lon_min - lon_pad, lon_max + lon_pad)
    lat_range = (lat_min - lat_pad, lat_max + lat_pad)

    # Aspect correction for latitude
    mid_lat = (lat_range[0] + lat_range[1]) / 2
    cos_corr = np.cos(np.radians(mid_lat))
    data_w = (lon_range[1] - lon_range[0]) * cos_corr
    data_h = lat_range[1] - lat_range[0]
    fig_h = args.width / (data_w / data_h)

    fig, ax = plt.subplots(figsize=(args.width, fig_h), facecolor=args.bg)
    ax.set_facecolor(args.bg)
    ax.set_aspect(1.0 / cos_corr)
    ax.axis("off")
    ax.set_xlim(*lon_range)
    ax.set_ylim(*lat_range)

    # Draw base (non-top-N) trees first in gray
    top_set = set(genus_names)
    base_mask = np.array([g not in top_set for g in genus])
    ax.scatter(lon[base_mask], lat[base_mask], s=args.dot_size, c=BASE_COLOR,
               alpha=args.dot_alpha, linewidths=0, rasterized=True)

    # Draw top genera on top, in reverse order (most common last = on top)
    for g in reversed(genus_names):
        mask = genus == g
        ax.scatter(lon[mask], lat[mask], s=args.dot_size, c=genus_color_map[g],
                   alpha=args.dot_alpha, linewidths=0, rasterized=True)

    # Legend
    genus_counts = {r[0]: r[1] for r in top_genera}
    for g in genus_names:
        common = genus_common.get(g, "")
        hint = f"  ({common.title()})" if common else ""
        ax.scatter([], [], s=40, c=genus_color_map[g], linewidths=0,
                   label=f"{g}  ({genus_counts[g]:,}){hint}")
    legend = ax.legend(
        loc="lower right", fontsize=11, frameon=True,
        facecolor=args.bg, edgecolor="none", framealpha=0.8,
        markerscale=2.5, labelcolor="#b8b8b8",
        borderpad=1.0, labelspacing=0.7, handletextpad=0.6,
    )
    legend.set_zorder(100)
    for text in legend.get_texts():
        text.set_fontfamily(font_family)

    # Title + subtitle
    title = args.title or "London, Revealed by Trees"
    spaced_title = "\u2009".join(title)
    fig.text(0.5, 0.97, spaced_title, fontfamily=font_family,
             fontsize=24, color="#C9CDD3", ha="center", va="top")
    fig.text(0.5, 0.943, "Street-tree records across the city, colored by genus",
             ha="center", va="top",
             fontsize=12, color="#707a70", fontfamily=font_family)

    # Citation
    fig.text(0.5, 0.015,
             "Data: London Datastore — Public Realm Trees  (data.london.gov.uk/dataset/2r45m)",
             ha="center", va="bottom",
             fontsize=8, color="#505a50", fontfamily=font_family)

    plt.subplots_adjust(left=0, right=1, top=0.93, bottom=0.03)
    out_path = args.output or f"{args.city.lower()}_top{args.top_n}_genus.png"
    fig.savefig(out_path, dpi=args.dpi, facecolor=args.bg, bbox_inches="tight", pad_inches=0.2)
    plt.close(fig)
    print(f"Saved → {out_path}")


if __name__ == "__main__":
    main()
