#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Portland's tree inventory (street trees and park trees).

Both layers are MapServer layers on portlandmaps.com with no `editingInfo`,
so the probe takes the latest of four stamps:

* Street Tree Inventory - Active Records (layer 1415): no edit-date column,
  so MAX(`Date_Inventoried`) (2024-10-28 when the city was wired) and the
  City of Portland Hub catalogue's `modified` for the layer (2025-01-23).  The
  layer is republished whole -- its OBJECTIDs run 1..N with no gaps -- and a
  republish moves the catalogue stamp even when no new inventory date does.
* Parks Tree Inventory (layer 220): MAX(`last_edited_date`) (editor
  tracking; 2024-08-20) and its Hub `modified` (2024-09-10).

The maximum, not a choice, because the catalogue stamp can fail to move on an
in-place overwrite and the data stamp can fail to move on a reload
(`hub_last_modified`'s docstring).  `field_max` and `hub_last_modified` raise
on a missing field or an unlisted layer -- a schema question -- and degrade on
an outage.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, field_max, hub_last_modified
from shared.ingest import emit_freshness

HUB = "gis-pdx.opendata.arcgis.com"
SERVICE = "https://www.portlandmaps.com/od/rest/services/COP_OpenData_Environment/MapServer"
STREET = FeatureLayer(f"{SERVICE}/1415", timeout=120)
PARKS = FeatureLayer(f"{SERVICE}/220", timeout=120)


def fetch_modified_at() -> datetime:
    return max(
        field_max(STREET, "Date_Inventoried"),
        hub_last_modified(HUB, STREET.base),
        field_max(PARKS, "last_edited_date"),
        hub_last_modified(HUB, PARKS.base),
    )


if __name__ == "__main__":
    emit_freshness("USPOR", fetch_modified_at)
