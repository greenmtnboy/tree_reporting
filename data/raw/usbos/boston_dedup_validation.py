#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "numpy", "scipy"]
# ///
"""Ground-truth validation for the OSM dedup grid in boston_tree_info.preql.

Boston counterpart of ustem/tempe_dedup_validation.py — see that script and
EXTENDING.md for the method.  Run before trusting a cell size copied from
another city: the duplicate/neighbor break sits wherever this city's OSM
positional offsets end and its planting spacing begins.

Inventory anchors come from the published usbos parquet on GCS (all municipal
sub-sources plus community — the same non-OSM anchor set the model's filtered
aggregate uses), OSM points from the committed staging parquet.  Also reports
`osm_ref` coverage: refs enable exact-id dedup where populated.
"""

import io
import math
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
import requests
from scipy.spatial import cKDTree

STAGING = Path(__file__).parent / "usbos_osm_staging.parquet"
INVENTORY_URL = "https://storage.googleapis.com/trilogy_public_models/duckdb/trees/usbos_tree_info_v2.parquet"
BOSTON_LAT = 42.36


def local_xy(lat, lon, lat0):
    m_lat = 111320.0
    m_lon = 111320.0 * math.cos(math.radians(lat0))
    return np.column_stack([lon * m_lon, lat * m_lat])


def haversine_m(lat1, lon1, lat2, lon2):
    R = 6371000.0
    p1, p2 = np.radians(lat1), np.radians(lat2)
    a = np.sin((p2 - p1) / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(np.radians(lon2 - lon1) / 2) ** 2
    return 2 * R * np.arcsin(np.sqrt(a))


def grid_flags(osm_lon, osm_lat, muni_lon, muni_lat, cell_m):
    cell_lat = cell_m / 111320.0
    cell_lon = cell_m / (111320.0 * math.cos(math.radians(BOSTON_LAT)))
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
    t = pq.read_table(STAGING, columns=["latitude", "longitude", "osm_ref"])
    osm_lat = np.array(t["latitude"].to_pylist(), dtype=float)
    osm_lon = np.array(t["longitude"].to_pylist(), dtype=float)
    refs = [r for r in t["osm_ref"].to_pylist() if r]
    print(f"OSM: {len(osm_lat)}, with osm_ref: {len(refs)}")

    r = requests.get(INVENTORY_URL, timeout=300)
    r.raise_for_status()
    inv = pq.read_table(io.BytesIO(r.content), columns=["latitude", "longitude", "data_source"])
    src = np.array(inv["data_source"].to_pylist(), dtype=object)
    lat = np.array([v if v is not None else np.nan for v in inv["latitude"].to_pylist()], dtype=float)
    lon = np.array([v if v is not None else np.nan for v in inv["longitude"].to_pylist()], dtype=float)
    keep = ~np.isnan(lat) & ~np.isnan(lon) & (src != "OSM_USBOS")
    muni_lat, muni_lon = lat[keep], lon[keep]
    print(f"inventory anchors: {len(muni_lat)} (by source: "
          + ", ".join(f"{s}={int((src[keep] == s).sum())}" for s in sorted(set(src[keep]))) + ")")

    lat0 = float(np.mean(muni_lat))
    muni_xy = local_xy(muni_lat, muni_lon, lat0)
    osm_xy = local_xy(osm_lat, osm_lon, lat0)
    mtree = cKDTree(muni_xy)
    otree = cKDTree(osm_xy)

    print("\n=== Inventory planting spacing (inventory -> nearest OTHER inventory), m ===")
    spacing = mtree.query(muni_xy, k=2)[0][:, 1]
    for p in (10, 25, 50, 75, 90):
        print(f"  p{p:02d}: {np.percentile(spacing, p):5.1f}")
    for rr in (5, 10, 20):
        print(f"  inventory trees with an inventory neighbor within {rr:2d}m: {(spacing <= rr).mean():.1%}")

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
