#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["duckdb", "numpy", "scipy", "pyarrow", "pytrilogy"]
# ///
"""Ground-truth calibration for a city's OSM dedup grid.

    cd data/raw && uv run osm_dedup_validation.py --city USSFO

NOT part of the refresh pipeline — a standalone analysis, run once per city
before trusting a cell size, and again whenever the inventory or the staged OSM
extract changes materially.

**Why a cell size cannot be copied between cities.** Distance alone cannot tell
a re-mapped inventory tree from the next tree in a planted row: Tempe's
inventory has a *median* nearest-neighbour spacing of 6.5m, so most inventory
trees have another inventory tree within 10m. What separates the populations is
pair *structure*:

- a **duplicate** is a mutual nearest neighbour of its match and hugs it far
  more tightly than the runner-up (small d1/d2), because it is the same tree
  re-mapped with a GPS or imagery offset;
- an **interstitial neighbour** sits at roughly planting spacing from both
  sides, so d1/d2 approaches 1 and mutual-NN collapses.

The cell size should sit at the break between those regimes, which is where a
city's OSM positional error ends and its planting spacing begins. Tempe breaks
at ~5m (mutual-NN >=88% below 5m, 25% in the 5-10m band) and so uses a 10m cell
— a 5m guarantee. A city traced from misaligned imagery will break later.

Reads OSM from the staged parquet in GCS and inventory anchors from the
published city parquet, filtered to non-OSM sources — the same anchor set the
model's filtered aggregate uses. Also reports `osm_ref` coverage, since refs
allow exact-id dedup where they are populated.
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import duckdb
import numpy as np
from scipy.spatial import cKDTree

sys.path.insert(0, str(Path(__file__).parent))
from _ingest_shared import CITY_BOUNDS, staging_url  # noqa: E402

TREES_URL = "https://storage.googleapis.com/trilogy_public_models/duckdb/trees"


def local_xy(lat, lon, lat0):
    """Equirectangular projection to metres, good enough over a city."""
    return np.column_stack(
        [lon * 111320.0 * math.cos(math.radians(lat0)), lat * 111320.0]
    )


def haversine_m(lat1, lon1, lat2, lon2):
    R = 6371000.0
    p1, p2 = np.radians(lat1), np.radians(lat2)
    a = (
        np.sin((p2 - p1) / 2) ** 2
        + np.cos(p1) * np.cos(p2) * np.sin(np.radians(lon2 - lon1) / 2) ** 2
    )
    return 2 * R * np.arcsin(np.sqrt(a))


def grid_flags(osm_lon, osm_lat, muni_lon, muni_lat, cell_m, city_lat):
    """Simulate the preql 4-staggered-grid scheme at a given cell size."""
    cell_lat = cell_m / 111320.0
    cell_lon = cell_m / (111320.0 * math.cos(math.radians(city_lat)))
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
                ((x, y) in muni_cells for x, y in zip(cx, cy)),
                dtype=bool,
                count=len(cx),
            )
    return flagged


def load(city_code: str, osm_path: str | None = None, inventory_path: str | None = None):
    """OSM points from staging, inventory anchors from the published parquet.

    Either side can be pointed at a local file instead, which is what a city
    being *added* needs: the published parquet and the staged extract are both
    outputs of the pipeline this cell size is an input to, so calibrating from
    GCS alone is a deadlock -- you cannot write the model without the number,
    and you cannot get the number without having run the model.  Run the city's
    ingest and `_osm_shared.fetch_osm_trees` to local parquets, calibrate
    against those, then wire the answer in.

    A local inventory file has not been through the model, so it carries no
    `data_source` column filter to apply -- and it does not need one: it is the
    municipal ingest's own output, which is all-municipal by construction.
    """
    con = duckdb.connect()
    con.execute("install httpfs; load httpfs;")
    staged = osm_path or f"{staging_url(f'{city_code.lower()}_osm_staging.parquet')}?cb=1"
    osm = con.execute(
        f"SELECT latitude, longitude, osm_ref FROM read_parquet('{staged}') "
        "WHERE latitude IS NOT NULL AND longitude IS NOT NULL"
    ).fetchnumpy()
    if inventory_path:
        published, anchors_only = inventory_path, ""
    else:
        published = f"{TREES_URL}/{city_code.lower()}_tree_info_v2.parquet?cb=1"
        anchors_only = "data_source NOT LIKE 'OSM_%' AND"
    muni = con.execute(
        f"SELECT latitude, longitude FROM read_parquet('{published}') "
        f"WHERE {anchors_only} "
        "latitude IS NOT NULL AND longitude IS NOT NULL"
    ).fetchnumpy()
    return osm, muni


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--city", required=True, help="City code, e.g. USSFO")
    ap.add_argument(
        "--cells",
        default="10,15,20",
        help="Comma-separated cell sizes in metres to simulate (default 10,15,20)",
    )
    ap.add_argument(
        "--osm-parquet",
        help="Local OSM parquet to read instead of the staged GCS object "
        "(for a city being added, whose staging object does not exist yet)",
    )
    ap.add_argument(
        "--inventory-parquet",
        help="Local municipal parquet to read instead of the published city "
        "parquet (same bootstrap case; assumed all-municipal)",
    )
    args = ap.parse_args()
    code = args.city.upper()
    lat_min, lat_max, _, _ = CITY_BOUNDS[code]
    city_lat = (lat_min + lat_max) / 2

    osm, muni = load(code, args.osm_parquet, args.inventory_parquet)
    osm_lat = np.asarray(osm["latitude"], dtype=float)
    osm_lon = np.asarray(osm["longitude"], dtype=float)
    muni_lat = np.asarray(muni["latitude"], dtype=float)
    muni_lon = np.asarray(muni["longitude"], dtype=float)
    # duckdb's fetchnumpy returns a masked array for a nullable VARCHAR, and a
    # masked entry is not None -- counting it that way reported every row as
    # ref-tagged. Ask the mask.
    refs = osm["osm_ref"]
    mask = getattr(refs, "mask", None)
    n_ref = int(
        sum(
            1
            for i, r in enumerate(refs)
            if not (mask is not None and mask[i]) and r is not None and str(r) != ""
        )
    )

    print(f"=== {code} ===")
    print(f"OSM: {len(osm_lat):,}   inventory anchors: {len(muni_lat):,}")
    print(f"osm_ref populated: {n_ref:,} ({n_ref / max(len(osm_lat), 1):.1%})")
    if len(osm_lat) == 0 or len(muni_lat) == 0:
        print("nothing to calibrate")
        return

    lat0 = float(np.mean(muni_lat))
    muni_xy = local_xy(muni_lat, muni_lon, lat0)
    osm_xy = local_xy(osm_lat, osm_lon, lat0)
    mtree, otree = cKDTree(muni_xy), cKDTree(osm_xy)

    print("\n--- Inventory planting spacing (inventory -> nearest OTHER inventory), m")
    spacing = mtree.query(muni_xy, k=2)[0][:, 1]
    print("  " + "  ".join(f"p{p:02d}={np.percentile(spacing, p):.1f}" for p in (10, 25, 50, 75, 90)))

    d_om, idx_om = mtree.query(osm_xy, k=2)
    nn_idx = idx_om[:, 0]
    d1 = haversine_m(osm_lat, osm_lon, muni_lat[nn_idx], muni_lon[nn_idx])
    d2 = d_om[:, 1]
    mutual = otree.query(muni_xy, k=1)[1][nn_idx] == np.arange(len(osm_lat))

    print("\n--- OSM -> nearest inventory tree: duplicate-vs-neighbour structure")
    print("  band        n    mutual-NN   median d1/d2")
    for lo, hi in [(0, 2), (2, 5), (5, 10), (10, 20), (20, 28)]:
        m = (d1 > lo) & (d1 <= hi)
        n = int(m.sum())
        if n:
            print(
                f"  {lo:2d}-{hi:2d}m {n:6d}     {mutual[m].mean():6.1%}       "
                f"{np.median(d1[m] / np.maximum(d2[m], 1e-9)):5.2f}"
            )

    print("\n--- 4-staggered-grid scheme vs ground truth, by cell size")
    print("  cell_m  flagged  missed<=half  flagged>diag")
    for cell_m in [int(c) for c in args.cells.split(",")]:
        fl = grid_flags(osm_lon, osm_lat, muni_lon, muni_lat, cell_m, city_lat)
        half, diag = cell_m / 2.0, cell_m * math.sqrt(2)
        print(
            f"  {cell_m:4d}    {int(fl.sum()):6d}       "
            f"{int(((d1 <= half) & ~fl).sum()):4d}          "
            f"{int(((d1 > diag) & fl).sum()):4d}"
        )

    # Where the break sits, reported so the caller does not have to eyeball the
    # table -- but the threshold is deliberately NOT 50%.
    #
    # The two errors are not symmetric.  A missed duplicate double-renders one
    # visible, toggleable dot; a false flag *hides a real tree*.  So a band that
    # is a coin flip should be left unflagged: at 52% mutual-NN, flagging it
    # hides about as many real trees as duplicates it removes.  London sits at
    # 51.5% over 18,076 rows and New York at 53.3% over 5,059 -- a bare 50% cut
    # sent both to a 20m cell, which would have hidden roughly 8,800 and 2,400
    # real trees respectively.
    #
    # Only flag a band that is clearly duplicate-dominated.
    CONFIDENT = 0.60
    print("\n--- suggested guarantee")
    for lo, hi in [(0, 2), (2, 5), (5, 10), (10, 20)]:
        m = (d1 > lo) & (d1 <= hi)
        n = int(m.sum())
        if not n:
            continue
        rate = mutual[m].mean()
        if rate < CONFIDENT:
            note = "coin flip" if rate >= 0.45 else "neighbour-dominated"
            print(f"  mutual-NN is {rate:.1%} in the {lo}-{hi}m band ({note}, n={n})")
            print(f"  -> guarantee {lo}m, i.e. a {lo * 2}m cell")
            break
    else:
        print("  mutual-NN stays above 60% through 20m; inspect manually")


if __name__ == "__main__":
    main()
