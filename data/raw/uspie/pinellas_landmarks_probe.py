#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Freshness probe for Pinellas County, Florida's landmarks.

The latest of two stamps, so the weekly landmark lane only rebuilds the
County's parquet when a register has changed:

* the County's `Pinellas_HistoricLandmarkUninc_view` layer: its
  `editingInfo.dataLastEditDate` (`layer_last_edit`);
* the NPS National Register rows with `County = Pinellas`, `State = FLORIDA`:
  the newest `EDIT_DATE` over them.

No stamp raises (a schema question); an outage degrades through
`emit_freshness`.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_ms_to_datetime, iter_features, layer_last_edit  # noqa: E402
from shared.ingest import emit_freshness  # noqa: E402
from shared.nrhp import NRHP_LAYERS  # noqa: E402

LOCAL = FeatureLayer(
    "https://services.arcgis.com/f5HgUpxURgEzTccH/arcgis/rest/services/"
    "Pinellas_HistoricLandmarkUninc_view/FeatureServer/0"
)
NRHP_WHERE = "County = 'Pinellas' AND State = 'FLORIDA'"


def national_register_last_edit() -> datetime:
    stamps = [
        esri_ms_to_datetime((feature.get("attributes") or {}).get("EDIT_DATE"))
        for layer in NRHP_LAYERS
        for page in iter_features(layer, out_fields="OBJECTID,EDIT_DATE", where=NRHP_WHERE)
        for feature in page
    ]
    stamps = [s for s in stamps if s is not None]
    if not stamps:
        raise RuntimeError("no National Register EDIT_DATE in Pinellas County")
    return max(stamps)


def fetch_modified_at() -> datetime:
    return max(layer_last_edit(LOCAL), national_register_last_edit())


if __name__ == "__main__":
    emit_freshness("USPIE", fetch_modified_at, label="USPIE landmarks")
