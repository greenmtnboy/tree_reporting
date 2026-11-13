"""Local temperature anomalies and descriptive water context on the fixed grid."""
import numpy as np
from scipy.stats import spearmanr, pearsonr
from shapely.geometry import box

from boston_tree_heat_grid import CELL_M, VIEW_M


def snapped_view(east, north, view_m=VIEW_M):
    """Whole global cells, with the closest permissible center to the landmark."""
    if view_m <= 0 or view_m % CELL_M:
        raise ValueError("View size must be a positive multiple of the cell size")
    w, s = np.floor((np.array([east, north])-view_m/2)/CELL_M+.5)*CELL_M
    return [float(w), float(s), float(w+view_m), float(s+view_m)]


def association(x, y):
    x, y = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    valid = np.isfinite(x) & np.isfinite(y)
    x, y = x[valid], y[valid]
    if len(x) < 6 or np.ptp(x) == 0 or np.ptp(y) == 0:
        return {"n": len(x), "spearman": None, "pearson": None,
                "reason": "Fewer than six cells or constant values"}
    return {"n": len(x), "spearman": float(spearmanr(x, y).statistic),
            "pearson": float(pearsonr(x, y).statistic)}


def assign_regions(cells, views):
    """Give each plotted cell one covering view, resolving overlap by distance."""
    owners = {}
    for cell in cells:
        x, y = cell["west"]+CELL_M/2, cell["south"]+CELL_M/2
        candidates = []
        for view in views:
            w, s, e, n = view["bounds_utm"]
            if w <= cell["west"] and s <= cell["south"] and cell["west"]+CELL_M <= e and cell["south"]+CELL_M <= n:
                candidates.append(((x-(w+e)/2)**2+(y-(s+n)/2)**2, view["place"]))
        owners[cell["id"]] = min(candidates)[1] if candidates else None
    return owners


def augment_local(result, data, anchors=None, view_m=VIEW_M, bounds_by_place=None):
    """Retain the metro sample; use all qualifying local cells, not extrema."""
    nearby_water = {}
    for r in result["cells"]:
        if r["eligible"] and "nearby_water_percent" not in r:
            polygon = box(r["west"], r["south"], r["west"]+CELL_M, r["south"]+CELL_M)
            neighborhood = polygon.buffer(500, quad_segs=16)
            nearby_water[r["id"]] = float(100*neighborhood.intersection(data["water_utm"]).area/neighborhood.area)
            r["nearby_water_percent"] = nearby_water[r["id"]]
    views = []
    landmarks = (result["examples"] if anchors is None else
                 [{"place": place, "anchor_lonlat": xy} for place, xy in anchors.items()])
    for old in landmarks:
        ae, an = data["forward"].transform(*old["anchor_lonlat"])
        bounds = (bounds_by_place[old["place"]] if bounds_by_place is not None
                  else snapped_view(ae, an, view_m))
        w, s, e, n = bounds
        if e-w != view_m or n-s != view_m or any(v % CELL_M for v in bounds):
            raise ValueError("View bounds must use whole cells and the declared view size")
        pixels = ((data["east"]>=w) & (data["east"]<e)
                  & (data["north"]>=s) & (data["north"]<n))
        valid = pixels & np.isfinite(data["mean"])
        land = pixels & ~data["wet"]
        if not valid.any():
            raise ValueError(f"No usable heat pixels for {old['place']}")
        baseline = float(data["mean"][valid].mean(dtype=np.float64))
        full = [r for r in result["cells"] if r["west"]>=w and r["south"]>=s
                and r["west"]+CELL_M<=e and r["south"]+CELL_M<=n]
        cells = [{**r, "temperature_delta": r["temperature"]-baseline}
                 for r in full if r["eligible"]]
        deltas = [r["temperature_delta"] for r in cells]
        view = {"place": old["place"], "anchor_lonlat": old["anchor_lonlat"],
                "bounds_utm": bounds, "baseline_temperature": baseline,
                "baseline_valid_land_fraction": float(valid.sum()/land.sum()),
                "baseline_valid_pixels": int(valid.sum()), "n": len(cells),
                "cells": cells, "all_cell_ids": [r["id"] for r in full],
                "tree_heat_association": association([r["tree_density"] for r in cells], deltas),
                "water_heat_association": association([r["nearby_water_percent"] for r in cells], deltas),
                "anchor_offset_m": float(np.hypot((w+e)/2-ae, (s+n)/2-an))}
        views.append(view)
    views.sort(key=lambda v: v["baseline_temperature"], reverse=True)
    for i, v in enumerate(views):
        v["letter"] = chr(65+i)
    result["local_views"] = views
    result["local_method"] = {
        "baseline": f"Mean of all available land pixels in the {view_m/1000:g} km view, each valid on all five dates; baseline coverage reported per view",
        "delta": "Cell mean surface temperature minus view baseline, degrees Celsius; no division or z-score",
        "window": f"{int(view_m/CELL_M)} x {int(view_m/CELL_M)} whole global 500 m cells; " + ("explicit audited bounds" if bounds_by_place is not None else "nearest permissible center to fixed landmark, max offset 354 m"),
        "selection": "Every qualifying cell in each view, using the metro sample's criteria; no hot/cool selection. " + result.get("eligibility", "Upstream eligibility retained."),
        "water": "Mapped open-water area / area of the square plus a 500 m rounded buffer; includes water inside the square; not a cooling-effect estimate",
        "associations": "Spearman and Pearson on local qualifying cells; suppressed for n<6 or constant values; water association unadjusted for tree density or land use",
        "interpretation": "Subtracting a common baseline does not change within-view correlations. Nearby views reuse cells; local samples are small and spatially correlated.",
    }
    for v in views:
        print(f"Local {v['letter']} {v['place']}: n={v['n']}, mean={v['baseline_temperature']:.2f}C, "
              f"heat coverage={v['baseline_valid_land_fraction']:.0%}, "
              f"tree rho={v['tree_heat_association']['spearman']}, water rho={v['water_heat_association']['spearman']}", flush=True)
    return views
