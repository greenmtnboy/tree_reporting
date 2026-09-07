#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Ajax's tree inventory.

The layer publishes no `editingInfo`, but it carries `LAST_EDITED_DATE`, so
this is the second of the three watermarks rather than the Hub-catalogue
fallback -- which matters here more than usual: the Hub's `modified` stamp for
this dataset reads 2021-05-09, and following it would have frozen Ajax on its
first build.  The column is live.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from _arcgis_shared import FeatureLayer, field_max
from _ingest_shared import emit_freshness

LAYER = FeatureLayer(
    "https://ajaxmaps.ajax.ca/gisernie/rest/services/Public/"
    "Ajax_Open_Data/MapServer/8"
)


def fetch_modified_at() -> datetime:
    return field_max(LAYER, "LAST_EDITED_DATE")


if __name__ == "__main__":
    emit_freshness("CAAJX", fetch_modified_at)
