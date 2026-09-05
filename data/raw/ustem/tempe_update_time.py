#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Tempe's tree inventory."""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from _arcgis_shared import FeatureLayer, layer_last_edit
from _ingest_shared import emit_freshness

LAYER = FeatureLayer(
    "https://services.arcgis.com/lQySeXwbBg53XWDi/arcgis/rest/services/"
    "TempeGuadalupe_CanopyCover2019_TreeInv_2021_Tempe_iTreeResults_TempeAZOct_2021_1"
    "/FeatureServer/0"
)


def fetch_modified_at() -> datetime:
    return layer_last_edit(LAYER)


if __name__ == "__main__":
    emit_freshness("USTEM", fetch_modified_at)
