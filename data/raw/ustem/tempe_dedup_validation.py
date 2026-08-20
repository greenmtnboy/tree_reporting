#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "numpy", "scipy", "pytrilogy"]
# ///
"""Ground-truth validation for the OSM dedup grid in tempe_tree_info.preql.

Not part of the refresh pipeline — a standalone analysis to (re)ground the
grid cell size whenever the inventory or the OSM staging file changes, and a
template for calibrating other cities before copying Tempe's cell size.

Answers two questions against exact haversine distances:

1. Does the staggered 4-grid scheme behave as designed at a given cell size
   (nothing missed within half a cell, nothing flagged beyond a diagonal)?
2. Are the flagged OSM points the *same* trees as their inventory match, or
   distinct neighbors at planting-row spacing?  Distance alone cannot tell
   (Tempe's inventory has 6.5m median spacing); pair structure can — a
   duplicate is a mutual nearest neighbor of its match and hugs it far more
   tightly than the runner-up, while an interstitial neighbor sits at
   ~spacing from both sides.

The 10m cell in tempe_tree_info.preql comes from this script's output: the
mutual-NN rate breaks from >=88% below 5m to 25% in the 5-10m band, so
matches beyond ~5m are mostly real neighbors, not re-mapped inventory.
"""

import math
import sys
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
from scipy.spatial import cKDTree

sys.path.insert(0, str(Path(__file__).parent))
from tempe_tree_info import download_geojson, web_mercator_to_wgs84

STAGING = Path(__file__).parent / "ustem_osm_staging.parquet"
TEMPE_LAT = 33.4


def local_xy(lat, lon, lat0):
    """Equirectangular projection to metres — exact to ~mm at sub-100m scales."""
    m_lat = 111320.0
    m_lon = 111320.0 * math.cos(math.radians(lat0))
    return np.column_stack([lon * m_lon, lat * m_lat])


def haversine_m(lat1, lon1, lat2, lon2):
    R = 6371000.0
    p1, p2 = np.radians(lat1), np.radians(lat2)
    a = np.sin((p2 - p1) / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(np.radians(lon2 - lon1) / 2) ** 2
    return 2 * R * np.arcsin(np.sqrt(a))


def grid_flags(osm_lon, osm_lat, muni_lon, muni_lat, cell_m):
    """Simulate the preql 4-staggered-grid scheme at a given cell size."""
    cell_lat = cell_m / 111320.0
    cell_lon = cell_m / (111320.0 * math.cos(math.radians(TEMPE_LAT)))
    flagged = np.zeros(len(osm_lat), dtype=bool)
    for ox in (0.0, 0.5):
        for oy in (0.0, 0.5):
            muni_cells = set(
                zip(
                    np.floor(muni_lon / cell_lon + ox).astype(np.int64),
                    np.floor(muni_lat / cell_lat + oy).astype(np.int64),
                )
            )
            cx = np.floor(osm_lon / cell_lon + ox).astype(np.int64)
            cy = np.floor(osm_lat / cell_lat + oy).astype(np.int64)
            flagged |= np.fromiter(
                ((x, y) in muni_cells for x, y in zip(cx, cy)), dtype=bool, count=len(cx)
            )
    return flagged


def main():
    t = pq.read_table(STAGING, columns=["latitude", "longitude"])
    osm_lat = np.array(t["latitude"].to_pylist(), dtype=float)
    osm_lon = np.array(t["longitude"].to_pylist(), dtype=float)

    payload = download_geojson()
    lats, lons = [], []
    for f in payload["features"]:
        coords = (f.get("geometry") or {}).get("coordinates") or [None, None]
        if coords[0] is None or coords[1] is None:
            continue
        lon, lat = web_mercator_to_wgs84(coords[0], coords[1])
        if -113 < lon < -111 and 33 < lat < 34:
            lats.append(lat)
            lons.append(lon)
    muni_lat, muni_lon = np.array(lats), np.array(lons)
    print(f"OSM: {len(osm_lat)}, inventory: {len(muni_lat)}")

    lat0 = float(np.mean(muni_lat))
    muni_xy = local_xy(muni_lat, muni_lon, lat0)
    osm_xy = local_xy(osm_lat, osm_lon, lat0)
    mtree = cKDTree(muni_xy)
    otree = cKDTree(osm_xy)

    print("\n=== Inventory planting spacing (inventory -> nearest OTHER inventory), m ===")
    spacing = mtree.query(muni_xy, k=2)[0][:, 1]
    for p in (10, 25, 50, 75, 90):
        print(f"  p{p:02d}: {np.percentile(spacing, p):5.1f}")
    for r in (5, 10, 20):
        print(f"  inventory trees with an inventory neighbor within {r:2d}m: {(spacing <= r).mean():.1%}")

    d_om, idx_om = mtree.query(osm_xy, k=2)
    nn_idx = idx_om[:, 0]
    d1 = haversine_m(osm_lat, osm_lon, muni_lat[nn_idx], muni_lon[nn_idx])
    d2 = d_om[:, 1]
    idx_mo = otree.query(muni_xy, k=1)[1]
    mutual = idx_mo[nn_idx] == np.arange(len(osm_lat))

    print("\n=== OSM -> nearest inventory tree: duplicate-vs-neighbor structure ===")
    print("  band       n    mutual-NN   median d1/d2")
    for lo, hi in [(0, 2), (2, 5), (5, 10), (10, 20), (20, 28)]:
        m = (d1 > lo) & (d1 <= hi)
        n = int(m.sum())
        if n:
            print(f"  {lo:2d}-{hi:2d}m {n:5d}     {mutual[m].mean():6.1%}       {np.median(d1[m] / d2[m]):5.2f}")

    print("\n=== 4-staggered-grid scheme vs ground truth, by cell size ===")
    print("  cell_m  flagged  missed<=half  flagged>diag")
    for cell_m in (10, 15, 20):
        fl = grid_flags(osm_lon, osm_lat, muni_lon, muni_lat, cell_m)
        half, diag = cell_m / 2.0, cell_m * math.sqrt(2)
        print(
            f"  {cell_m:4d}    {int(fl.sum()):6d}       {int(((d1 <= half) & ~fl).sum()):4d}"
            f"          {int(((d1 > diag) & fl).sum()):4d}"
        )

    print("\nDone.")


if __name__ == "__main__":
    main()
