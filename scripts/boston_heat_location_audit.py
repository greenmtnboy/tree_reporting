"""Coverage-first landmark audit; neither correlations nor heat means select views."""
import json
from pathlib import Path

import numpy as np

from boston_canopy_grid import matched_comparison
from boston_tree_heat_local import augment_local

# Keep the five well-covered original views; add recognizable, geographically
# varied windows with >=90% common land heat and >=20 usable canopy/heat cells.
# Harvard uses a more specific Church Street anchor; all windows still snap to
# the same fixed grid. No custom optimization of the boundary or correlation.
CHOSEN = ("Newmarket", "Nubian Square", "Corey Hill Park", "Brookline Village",
          "Larz Anderson Park", "Harvard Square", "Brighton Center", "Oak Square",
          "Herter Park", "Fresh Pond", "Hyde Square", "Newtonville")


def coverage_summary(view):
    return {"place": view["place"], "anchor_lonlat": view["anchor_lonlat"],
            "bounds_utm": view["bounds_utm"],
            "heat_coverage": view["baseline_valid_land_fraction"],
            "heat_cells": view["n"], "canopy_heat_cells": view["paired_n"],
            "all_cells": len(view["all_cell_ids"])}


def summarize_set(views):
    coverages = [v["baseline_valid_land_fraction"] for v in views]
    return {"views": len(views), "mean_view_heat_coverage": float(np.mean(coverages)),
            "min_view_heat_coverage": min(coverages),
            "views_below_90_percent": sum(c < .9 for c in coverages),
            "usable_cell_slots": sum(v["paired_n"] for v in views),
            "possible_cell_slots": sum(len(v["all_cell_ids"]) for v in views),
            "unique_usable_cells": len({c["id"] for v in views for c in v["paired_cells"]})}


def coverage_locations(result, data, original):
    catalog = json.loads(Path(__file__).with_name("boston_heat_location_candidates.json").read_text(encoding="utf-8"))
    candidates = augment_local(result, data, anchors={c["place"]: c["anchor_lonlat"] for c in catalog["candidates"]})
    for view in candidates:
        cells, scores = matched_comparison(view["cells"])
        view.update(paired_cells=cells, paired_n=len(cells), paired_associations=scores)
    lookup = {v["place"]: v for v in [*original, *candidates]}
    chosen = [lookup[name] for name in CHOSEN]
    for v in chosen:
        if v["baseline_valid_land_fraction"] < .9 or v["paired_n"] < 20 or len(v["all_cell_ids"]) != 25:
            raise ValueError(f"{v['place']} fails fixed coverage criteria; inspect rather than relaxing them")
    chosen.sort(key=lambda v: (-v["baseline_temperature"], v["place"]))
    for i, v in enumerate(chosen):
        v["letter"] = chr(65+i)
    result["location_audit"] = {
        "anchor_sources": catalog,
        "selection": "Retain five original well-covered anchors; replace seven low-coverage windows (including a precise Harvard Square re-anchor). Require >=90% view land heat, >=20 canopy/heat cells, all 25 cells in the study grid. Geography and recognizable landmarks choose among qualified candidates; correlation scores and mean temperatures are not selection criteria. Display order alone uses mean temperature.",
        "bias": "Better five-date heat coverage favors western metro locations. This is a coverage-selected illustration, not a geographically representative or independent sample; views overlap.",
        "original": [coverage_summary(v) for v in original],
        "candidates": [coverage_summary(v) for v in candidates],
        "chosen": [coverage_summary(v) for v in chosen],
        "before": summarize_set(original), "after": summarize_set(chosen),
        "unchanged": "Metro scatter sample, canopy footprints, heat dates, quality thresholds and per-cell measurements are unchanged.",
    }
    result["local_views"] = chosen
    result["local_method"]["landmark_selection"] = result["location_audit"]["selection"]
    print("Coverage audit:", json.dumps({"before": result["location_audit"]["before"],
                                       "after": result["location_audit"]["after"]}), flush=True)
    return chosen
