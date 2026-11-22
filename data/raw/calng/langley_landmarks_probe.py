#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Freshness probe for Langley Township's landmarks.

The later of the two sources' stamps:

* the Township's historic-places view publishes `editingInfo`; its
  `dataLastEditDate` (2024-08-02 when the city was wired) moves with every
  edit to the inventory;
* the Province's Heritage Conservation Areas register carries no edit stamp,
  so its newest `DESIGNATED_DATE` stands in -- province-wide, which moves
  only when some BC area is designated (rare, and costs one rebuild).

A missing stamp raises (a schema change); an outage degrades.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, layer_last_edit
from shared.platforms.wfs import WfsLayer, wfs_max_property
from shared.ingest import emit_freshness

INVENTORY = FeatureLayer(
    "https://services5.arcgis.com/frpHL0Fv8koQRVWY/arcgis/rest/services/"
    "LangleysHistoricPlaces_HFL_InventoriedSites_view/FeatureServer/0",
    timeout=60,
)
HCA = WfsLayer(
    "https://openmaps.gov.bc.ca/geo/pub/wfs",
    "pub:WHSE_HUMAN_CULTURAL_ECONOMIC.HIST_HERITAGE_CNSRV_AREAS_SV",
    timeout=60,
)


def fetch_modified_at() -> datetime:
    return max(layer_last_edit(INVENTORY), wfs_max_property(HCA, "DESIGNATED_DATE"))


if __name__ == "__main__":
    emit_freshness("CALNG", fetch_modified_at, label="CALNG landmarks")
