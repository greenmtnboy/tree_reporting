#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Freshness probe for Canberra's landmarks.

The later of the two registers' stamps, so the weekly landmark lane only
rebuilds Canberra's parquet when one of them has changed:

* the ACT Heritage Register layer: a hosted feature layer with `editingInfo`,
  so its `dataLastEditDate`;
* the Commonwealth Heritage List: no `editingInfo`, so the newest `UPDATE_`
  over its ACT rows.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, field_max, layer_last_edit
from shared.ingest import emit_freshness

ACT_REGISTER = FeatureLayer(
    "https://services1.arcgis.com/E5n4f1VY84i0xSjy/arcgis/rest/services/"
    "ACTGOV_Heritage_Register/FeatureServer/1"
)
CHL = FeatureLayer(
    "https://gis.environment.gov.au/gispubmap/rest/services/ogc_services/"
    "Commonwealth_Heritage_List/FeatureServer/0"
)


def fetch_modified_at() -> datetime:
    return max(
        layer_last_edit(ACT_REGISTER),
        field_max(CHL, "UPDATE_", where="STATE = 'ACT'"),
    )


if __name__ == "__main__":
    emit_freshness("AUCBR", fetch_modified_at, label="AUCBR landmarks")
