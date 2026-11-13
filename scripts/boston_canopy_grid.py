"""Matched density/crown-footprint comparisons on the existing 500 m heat grid."""
import hashlib
import json

import numpy as np
import shapely
from shapely.geometry import box

from analyze_boston_canopy_heat import OUT, crown_cover, crown_snapshot, sha
from boston_income_heat_data import CACHE, save_json
from boston_tree_heat_grid import CELL_M
from boston_tree_heat_local import association
from render_boston_sources_map import load_canopy_widths

MIN_CROWN_COMPLETENESS = .95
SELECTION = {
    "min_view_heat_coverage": .90,
    "min_paired_cells": 18,
    "min_retained_heat_cells": .80,
    "max_spearman_each": -.50,
    "rank": "Strongest weaker inverse Spearman of density and canopy; place name breaks ties",
    "warning": "Four deliberately selected, well-covered inverse-association examples; not representative or independent. Views can overlap.",
}


def matched_comparison(cells):
    """One explicit complete-case sample feeds BOTH correlations."""
    rows = [c for c in cells if c.get("eligible") and c.get("crown_completeness", 0) >= MIN_CROWN_COMPLETENESS
            and c.get("canopy_percent") is not None and np.isfinite(c["canopy_percent"])]
    return rows, {metric: association([r[metric] for r in rows], [r["temperature"] for r in rows])
                  for metric in ("tree_density", "canopy_percent")}


def select_four(views):
    candidates = []
    for v in views:
        scores = [v["paired_associations"][m]["spearman"] for m in ("tree_density", "canopy_percent")]
        if (v["baseline_valid_land_fraction"] >= SELECTION["min_view_heat_coverage"]
                and v["paired_n"] >= SELECTION["min_paired_cells"]
                and v["paired_n"]/max(1, v["n"]) >= SELECTION["min_retained_heat_cells"]
                and all(s is not None and s <= SELECTION["max_spearman_each"] for s in scores)):
            candidates.append((max(scores), v["place"], v))
    candidates.sort(key=lambda t: (t[0], t[1]))
    if len(candidates) < 4:
        raise ValueError(f"Only {len(candidates)} regions meet the declared selection rule; do not silently relax it")
    selected = [v for _, _, v in candidates[:4]]
    return sorted(selected, key=lambda v: (-v["baseline_temperature"], v["place"]))


def augment_canopy(result, data, views):
    snapshot = crown_snapshot()
    old = json.loads((OUT/"boston_canopy_heat.json").read_text())
    if sha(snapshot) != old["fingerprint"]["crown_sha256"]:
        raise ValueError("Crown snapshot differs from frozen cross-sectional analysis")
    fingerprint = {
        "algorithm": "500m-half-open-land-centers-unioned-crowns-v1",
        "source_sha256": sha(__file__),
        "union_source_sha256": sha(OUT.parent/"analyze_boston_canopy_heat.py"),
        "crs": result["crs"], "tree_sha256": result["provenance"]["tree_sha256"],
        "water_sha256": result["provenance"]["water"]["cache_sha256"],
        "crown_sha256": sha(snapshot), "resolution_m": 2,
        "cell_ids": [c["id"] for c in result["cells"] if c["eligible"]],
    }
    key = hashlib.sha256(json.dumps(fingerprint, sort_keys=True).encode()).hexdigest()[:16]
    path = CACHE/f"canopy-grid-{key}.json"
    if path.exists():
        cached = json.loads(path.read_text())
        if cached["fingerprint"] != fingerprint:
            raise ValueError("Canopy cache fingerprint mismatch")
        print("Using crown grid cache", path, flush=True)
    else:
        widths, source = load_canopy_widths(data["points"], np.ones(len(data["te"]), bool), str(snapshot))
        if np.nanmax(widths) > 100:
            raise ValueError("Unexpected >100m crown diameter; investigate instead of clamping")
        centers = shapely.points(data["te"], data["tn"])
        index = shapely.STRtree(centers)
        radii = widths/2
        max_radius = float(np.nanmax(radii))
        measured = {}
        for c in result["cells"]:
            if not c["eligible"]:
                continue
            w, s = c["west"], c["south"]
            square = box(w, s, w+CELL_M, s+CELL_M)
            land = square.difference(data["water_utm"])
            near = index.query(box(w-max_radius, s-max_radius, w+CELL_M+max_radius, s+CELL_M+max_radius))
            inside = near[(data["te"][near] >= w) & (data["te"][near] < w+CELL_M)
                          & (data["tn"][near] >= s) & (data["tn"][near] < s+CELL_M)
                          & data["tree_on_land"][near]]
            if len(inside) != c["trees"]:
                raise ValueError(f"Tree counts disagree for {c['id']}")
            valid = np.isfinite(widths[inside]) & (widths[inside] > 0)
            native = data["points"]["crown_width_m"][inside]
            n_native = int((np.isfinite(native) & (native > 0)).sum())
            area, raster_land = crown_cover(land, centers[near], radii[near], 2)
            fine_area, fine_land = crown_cover(land, centers[near], radii[near], 1)
            if not raster_land or not fine_land:
                raise ValueError(f"No raster land for eligible cell {c['id']}")
            measured[c["id"]] = {
                "crown_completeness": float(valid.mean()) if len(inside) else 1.,
                "crowns_with_width": int(valid.sum()), "source_widths": n_native,
                "fallback_widths": int(valid.sum())-n_native,
                "canopy_area_m2": area, "raster_land_m2": raster_land,
                "canopy_percent": 100*area/raster_land,
                "canopy_percent_1m": 100*fine_area/fine_land,
            }
            if len(measured) % 50 == 0:
                print(f"Crown unions: {len(measured)} / {len(fingerprint['cell_ids'])}", flush=True)
        cached = {"fingerprint": fingerprint, "cells": measured, "width_sources": source}
        save_json(path, cached)
    for c in result["cells"]:
        c.update(cached["cells"].get(c["id"], {}))
    rows, comparisons = matched_comparison(result["cells"])
    for v in views:
        for c in v["cells"]:
            c.update(cached["cells"][c["id"]])
        paired, scores = matched_comparison(v["cells"])
        v.update(paired_cells=paired, paired_n=len(paired), paired_associations=scores)
        print(f"Paired {v['place']}: {len(paired)}/{v['n']}, "
              f"density={scores['tree_density']['spearman']}, canopy={scores['canopy_percent']['spearman']}", flush=True)
    result["canopy_comparison"] = {
        "fingerprint": fingerprint, "cache": str(path), "width_sources": cached["width_sources"],
        "paired_n": len(rows), "paired_cell_ids": [r["id"] for r in rows], "associations": comparisons,
        "min_crown_completeness": MIN_CROWN_COMPLETENESS,
        "metric": "Union of estimated circular crowns / raster land area, percent. Overlaps counted once; crowns centered outside a cell may intersect it. 2m pixel centers; 1m sensitivity check.",
        "sources": "Published source widths (mostly aerial ML crowns), then cached DBH-model crown estimates for missing widths. Remaining missing crowns not imputed.",
        "sample": "Same heat-eligible cells in both comparisons, with widths for >=95% of mapped land tree centers. Completeness measures available widths, not tree-detection recall.",
        "raster_sensitivity_max_percentage_points": max(abs(r["canopy_percent"]-r["canopy_percent_1m"]) for r in rows),
        "selection_rule": SELECTION,
    }
    four = select_four(views)
    result["canopy_comparison"]["selected_four"] = [v["place"] for v in four]
    print("Metro paired comparison:", comparisons, flush=True)
    print("Selected four:", [v["place"] for v in four], flush=True)
    return rows, four
