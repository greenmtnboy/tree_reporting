#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Geneva's cantonal tree inventory (SITG `SIPV_ICA_ARBRE_ISOLE`).

The layer has no `editingInfo`, and the SITG catalogue's `dateModified`
moves every morning whether or not a tree changed (the metadata record is
regenerated daily), so neither is a watermark.  The register's own edit
stamp is `DATE_OBSERVATION`, the date of a tree's latest survey: a survey,
a new tree or a correction moves it.  MAX over the layer, restricted to dates
not in the future -- one row typed a year ahead would otherwise pin the
watermark there and hide every real edit until that day.  A tree deleted
without a new survey elsewhere does not move it; the next survey picks the
deletion up.

`DATE_OBSERVATION` is a day stored as midnight UTC (`datesInUnknownTimezone`
is false), so there is no wall-time to correct.  A transport failure or a
maintenance page degrades to the epoch through `emit_freshness`; an empty
statistics answer raises (the layer changed shape).
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit_freshness
from shared.platforms.arcgis import FeatureLayer, field_max

LAYER = FeatureLayer(
    "https://vector.sitg.ge.ch/arcgis/rest/services/SIPV_ICA_ARBRE_ISOLE/FeatureServer/0",
    timeout=60,
)


def fetch_modified_at() -> datetime:
    return field_max(LAYER, "DATE_OBSERVATION", where="DATE_OBSERVATION <= CURRENT_TIMESTAMP")


if __name__ == "__main__":
    emit_freshness("CHGVA", fetch_modified_at)
