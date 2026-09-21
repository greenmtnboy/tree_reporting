"""Human crown centers are detection-only labels, never background masks.

The geographic region UUID supplies an image-scoped stable identity. Crown
radius is observed metadata, not a DBH estimate or a matching tolerance.
"""
from __future__ import annotations

import pandas as pd
from pyproj import Transformer

from urban_tree_ml.config import ProjectConfig
from urban_tree_ml.splits import assign_spatial_splits


def confirmed_tree_rows(regions: list[dict], config: ProjectConfig, raster_crs) -> pd.DataFrame:
    forward = Transformer.from_crs("EPSG:4326", raster_crs, always_xy=True)
    inverse = Transformer.from_crs(raster_crs, "EPSG:4326", always_xy=True)
    rows = []
    for region in regions:
        if region["mode"] != "confirmed-tree" or region.get('tree_id'):
            continue
        x, y = forward.transform(region["anchor_longitude"], region["anchor_latitude"])
        longitude, latitude = inverse.transform(x + region["east_m"], y + region["north_m"])
        rows.append({
            "tree_id": f"manual-{region['region_id']}",
            "longitude": longitude, "latitude": latitude,
            "crown_radius_m": float(region["radius_m"]),
            "crown_source": "human",
            "species": region.get('species', ''),
            "identification_source": region.get('identification_source', ''),
            "dbh_log1p": None, "genus_id": -1, "species_id": -1,
            "dbh_eligible": False, "genus_eligible": False, "species_eligible": False,
            "feedback_excluded": False, "planted_after_imagery": False,
            "allowed_splits": region["splits"],
        })
    if not rows:
        return pd.DataFrame()
    frame = assign_spatial_splits(pd.DataFrame(rows), config.split, config.seed)
    # Never move human-reviewed material into sealed test blocks or across guards.
    allowed = [split in splits and split != "test"
               for split, splits in zip(frame["split"], frame["allowed_splits"], strict=True)]
    return frame[frame["split_eligible"] & pd.Series(allowed, index=frame.index)].drop(columns="allowed_splits")
