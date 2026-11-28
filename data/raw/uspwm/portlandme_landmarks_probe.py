#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Freshness probe for Portland, Maine's landmarks.

The latest of two stamps, so the weekly landmark lane only rebuilds
Portland's parquet when a register has changed:

* the City's `Local_Landmarks` layer: no `editingInfo` and no edit-date
  column, so its portal item's `modified` on the City's own ArcGIS
  Enterprise portal (gis.portlandmaine.gov/portal);
* the NPS National Register rows inside `CITY_BOUNDS["USPWM"]`
  (`shared.nrhp.national_register_modified_at`).
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import esri_ms_to_datetime  # noqa: E402
from shared.ingest import emit_freshness, get_json_with_retry  # noqa: E402
from shared.nrhp import national_register_modified_at  # noqa: E402

REGISTER_ITEM = "13d2773085ab41a1afad4f79d7b4b9d6"
ITEM_URL = f"https://gis.portlandmaine.gov/portal/sharing/rest/content/items/{REGISTER_ITEM}"


def register_item_modified() -> datetime:
    payload = get_json_with_retry(ITEM_URL, params={"f": "json"}, timeout=60)
    stamp = esri_ms_to_datetime(payload.get("modified"))
    if stamp is None:
        raise RuntimeError(f"no `modified` on Portland, ME portal item {REGISTER_ITEM}")
    return stamp


def fetch_modified_at() -> datetime:
    return max(register_item_modified(), national_register_modified_at("USPWM", "Portland"))


if __name__ == "__main__":
    emit_freshness("USPWM", fetch_modified_at, label="USPWM landmarks")
