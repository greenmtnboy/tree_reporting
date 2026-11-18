#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Freshness probe for Sydney's landmarks.

The later of the two registers' stamps, so the landmark lane only rebuilds
Sydney's parquet when one of them has changed.  Neither layer publishes
`editingInfo` (both are MapServer layers), so each is a `field_max`:

* the State Heritage Register: the newest `LAST_UPDATE` over Sydney's rows;
* the LEP heritage layer: the newest `CURRENCY_DATE` (the date the plan's
  data is current to) over the two Sydney plans.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, field_max
from shared.ingest import emit_freshness

SHR = FeatureLayer(
    "https://mapprod3.environment.nsw.gov.au/arcgis/rest/services/EDP/SHR_Curtilage/MapServer/0"
)
EPI = FeatureLayer(
    "https://mapprod3.environment.nsw.gov.au/arcgis/rest/services/Planning/"
    "EPI_Primary_Planning_Layers/MapServer/0"
)
EPI_WHERE = (
    "EPI_NAME IN ('Sydney Local Environmental Plan 2012', "
    "'Sydney Local Environmental Plan (Harold Park) 2011')"
)


def fetch_modified_at() -> datetime:
    return max(
        field_max(SHR, "LAST_UPDATE", where="LGA = 'SYDNEY'"),
        field_max(EPI, "CURRENCY_DATE", where=EPI_WHERE),
    )


if __name__ == "__main__":
    emit_freshness("AUSYD", fetch_modified_at, label="AUSYD landmarks")
