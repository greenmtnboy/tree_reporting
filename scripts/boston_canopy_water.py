"""Explicit majority-water exclusion for the canopy/heat poster."""
from boston_canopy_grid import matched_comparison
from boston_heat_location_audit import summarize_set


def is_majority_water(cell):
    # Keep exactly 50% water. This uses geometric area, not thermal missingness.
    return cell["land_fraction"] < .5


def apply_water_exclusion(result, views):
    """Filter both metro and local correlations, preserving land-heat baselines."""
    before = result["canopy_comparison"]
    old_ids = set(before["paired_cell_ids"])
    for cell in result["cells"]:
        cell["water_excluded"] = is_majority_water(cell)
    lookup = {c["id"]: c for c in result["cells"]}
    rows, associations = matched_comparison([c for c in result["cells"] if not c["water_excluded"]])
    result["water_filter"] = {
        "max_water_fraction": .5, "rule": "Exclude geometric water share >50%; exactly 50% retained",
        "before_n": before["paired_n"], "before_associations": before["associations"],
        "excluded_metro_cell_ids": sorted(c["id"] for c in result["cells"] if c["id"] in old_ids and c["water_excluded"]),
        "baseline": "View baseline remains the mean of all available common-five-date land pixels; water pixels were already excluded. Majority-water cells are removed from cell statistics and correlations, not from geographic context.",
    }
    before.update(paired_n=len(rows), paired_cell_ids=[c["id"] for c in rows], associations=associations)
    before["sample"] += " Cells with >50% geometric water area are excluded."
    for view in views:
        for cell in view["cells"]:
            cell["water_excluded"] = is_majority_water(cell)
        paired, scores = matched_comparison([c for c in view["cells"] if not c["water_excluded"]])
        view.update(paired_cells=paired, paired_n=len(paired), paired_associations=scores,
                    water_excluded_cell_ids=[key for key in view["all_cell_ids"] if lookup[key]["water_excluded"]])
    if "location_audit" in result:
        result["location_audit"]["after_water_filter"] = summarize_set(views)
        result["location_audit"]["water_filter_note"] = "Heat dates, canopy footprints and per-cell measurements are unchanged; metro/local correlations exclude majority-water cells."
    return rows
