#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["duckdb", "matplotlib", "numpy", "requests"]
# ///
"""London borough map: each borough's #1 genus highlighted in color, rest in gray.

Usage:
    uv run scripts/render_borough_genus_map.py
    uv run scripts/render_borough_genus_map.py --dpi 400 --output poster.png
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

REMOTE_BASE = "https://storage.googleapis.com/trilogy_public_models/duckdb/trees"
DATA_VERSION = 2

# One color per distinct genus — we'll assign dynamically
GENUS_PALETTE = [
    "#F07E82",  # coral red
    "#EDB85A",  # golden amber
    "#60F0BF",  # teal green
    "#92CFF8",  # steel blue
    "#D494E0",  # soft violet
    "#F0A54E",  # burnt orange
    "#88FFF4",  # aqua
    "#BDD468",  # olive lime
    "#E47EA0",  # rose
    "#A0F8C8",  # sage
    "#C89260",  # bronze
    "#B0E8FF",  # sky
]

BASE_COLOR = "#999999"


def load_inter_font() -> str:
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


def main() -> None:
    font_family = load_inter_font()

    parser = argparse.ArgumentParser()
    parser.add_argument("--dpi", type=int, default=300)
    parser.add_argument("--width", type=float, default=20)
    parser.add_argument("--dot-size", type=float, default=0.3)
    parser.add_argument("--dot-alpha", type=float, default=0.5)
    parser.add_argument("--bg", default="#0a0d10")
    parser.add_argument("--output", default=None)
    parser.add_argument("--title", default=None)
    args = parser.parse_args()

    con = duckdb.connect(":memory:")
    con.execute("INSTALL httpfs; LOAD httpfs;")

    url = f"{REMOTE_BASE}/gblon_tree_info_v{DATA_VERSION}.parquet"
    print(f"Fetching {url} ...")
    con.execute(f"""
        CREATE TABLE trees AS
        SELECT
            borough,
            split_part(species, ' ', 1) AS genus,
            latitude,
            longitude
        FROM '{url}'
        WHERE latitude IS NOT NULL AND longitude IS NOT NULL
    """)

    total = con.execute("SELECT count(*) FROM trees").fetchone()[0]
    print(f"Total trees: {total:,}")

    # Find top genus per borough
    top_per_borough = con.execute("""
        WITH counts AS (
            SELECT borough, genus, count(*) AS cnt
            FROM trees
            WHERE genus IS NOT NULL AND genus != ''
              AND borough IS NOT NULL AND borough != ''
            GROUP BY borough, genus
        ),
        ranked AS (
            SELECT *, row_number() OVER (PARTITION BY borough ORDER BY cnt DESC) AS rn
            FROM counts
        )
        SELECT borough, genus, cnt FROM ranked WHERE rn = 1
        ORDER BY cnt DESC
    """).fetchall()

    borough_top_genus = {r[0]: r[1] for r in top_per_borough}
    borough_counts = {r[0]: r[2] for r in top_per_borough}

    # Get distinct top genera (in order of total prevalence) for color assignment
    distinct_genera = list(dict.fromkeys(r[1] for r in top_per_borough))
    genus_color = {g: GENUS_PALETTE[i % len(GENUS_PALETTE)] for i, g in enumerate(distinct_genera)}

    print(f"Boroughs: {len(borough_top_genus)}")
    print(f"Distinct dominant genera: {', '.join(distinct_genera)}")
    for borough, genus in sorted(borough_top_genus.items()):
        print(f"  {borough}: {genus} ({borough_counts[borough]:,})")

    genus_common = {
        "Quercus": "Oak",
        "Acer": "Maple",
        "Prunus": "Plum",
        "Platanus": "Plane Tree",
        "Fraxinus": "Ash",
        "Tilia": "Linden",
        "Crataegus": "Hawthorn",
    }

    # Fetch all points with borough info
    all_rows = con.execute("""
        SELECT COALESCE(borough, '') AS borough,
               COALESCE(genus, '') AS genus,
               latitude, longitude
        FROM trees
    """).fetchall()

    boroughs = np.array([r[0] for r in all_rows])
    genera = np.array([r[1] for r in all_rows])
    lat = np.array([r[2] for r in all_rows], dtype=np.float64)
    lon = np.array([r[3] for r in all_rows], dtype=np.float64)

    # For each tree: colored if it matches its borough's top genus, gray otherwise
    is_highlighted = np.array([
        borough_top_genus.get(b) == g
        for b, g in zip(boroughs, genera)
    ])

    # Bounding box (negative pad to crop whitespace)
    pad = -0.03
    lon_min, lon_max = float(lon.min()), float(lon.max())
    lat_min, lat_max = float(lat.min()), float(lat.max())
    lon_pad = (lon_max - lon_min) * pad
    lat_pad = (lat_max - lat_min) * pad
    lon_range = (lon_min - lon_pad, lon_max + lon_pad)
    lat_range = (lat_min - lat_pad, lat_max + lat_pad)

    # Aspect correction
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

    # Layer 1: all non-highlighted trees in gray
    gray_mask = ~is_highlighted
    ax.scatter(lon[gray_mask], lat[gray_mask], s=args.dot_size, c=BASE_COLOR,
               alpha=args.dot_alpha * 0.625, linewidths=0, rasterized=True)

    # Layer 2: highlighted trees, colored by their genus
    for genus in reversed(distinct_genera):
        mask = is_highlighted & (genera == genus)
        if not mask.any():
            continue
        ax.scatter(lon[mask], lat[mask], s=args.dot_size, c=genus_color[genus],
                   alpha=min(args.dot_alpha * 1.44, 1.0), linewidths=0, rasterized=True)

    # Legend — sorted by borough frequency (most boroughs first)
    genus_borough_count = {g: sum(1 for v in borough_top_genus.values() if v == g) for g in distinct_genera}
    sorted_genera = sorted(distinct_genera, key=lambda g: genus_borough_count[g], reverse=True)
    for genus in sorted_genera:
        common = genus_common.get(genus, "")
        name = f"{genus} ({common})" if common else genus
        n = genus_borough_count[genus]
        ax.scatter([], [], s=40, c=genus_color[genus], linewidths=0,
                   label=f"{name}  —  {n} borough{'s' if n != 1 else ''}")

    legend = ax.legend(
        loc="lower right", bbox_to_anchor=(1.0, 0.15), fontsize=10, frameon=True,
        facecolor=args.bg, edgecolor="none", framealpha=0.8,
        markerscale=1.25, labelcolor="#d0d0d0",
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
    fig.text(0.5, 0.943, "Most common genus in each borough highlighted",
             ha="center", va="top",
             fontsize=12, color="#a0b0a0", fontfamily=font_family)

    # Citation
    fig.text(0.5, 0.015,
             "Data: London Datastore \u2014 Public Realm Trees  (data.london.gov.uk/dataset/2r45m)",
             ha="center", va="bottom",
             fontsize=8, color="#a0a8a0", fontfamily=font_family)

    plt.subplots_adjust(left=0, right=1, top=0.93, bottom=0.03)
    out_path = args.output or "gblon_borough_genus.png"
    fig.savefig(out_path, dpi=args.dpi, facecolor=args.bg, bbox_inches="tight", pad_inches=0.2)
    plt.close(fig)
    print(f"Saved \u2192 {out_path}")


if __name__ == "__main__":
    main()
