#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Freshness probe for Lethbridge's historic places register.

**The maximum of two stamps.**  The layer publishes no `editingInfo`; what it
carries is `DesignationDate`, whose maximum moves when the city designates a
new historic resource, and its Hub catalogue entry carries a `modified` stamp.
Neither alone is trustworthy -- a designation date misses an edit to an
existing entry, and the catalogue stamp can miss a service overwritten in
place -- so the later of the two wins.  See `_ckan_shared.data_last_modified`
for where that rule came from.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from _arcgis_shared import FeatureLayer, field_max, hub_last_modified
from _ingest_shared import UpstreamUnavailable, emit_freshness

HUB_HOST = "opendata.lethbridge.ca"
LAYER_URL = (
    "https://gis.lethbridge.ca/gisopendata/rest/services/OpenData/"
    "odl_historicplaces/MapServer/0"
)
LAYER = FeatureLayer(LAYER_URL)


def fetch_modified_at() -> datetime:
    """The later of the newest designation and the catalogue's own stamp."""
    stamps: list[datetime] = []
    errors: list[Exception] = []
    for read in (lambda: field_max(LAYER, "DesignationDate"),
                 lambda: hub_last_modified(HUB_HOST, LAYER_URL)):
        try:
            stamps.append(read())
        except UpstreamUnavailable as exc:
            errors.append(exc)
    if not stamps:
        raise errors[0]
    return max(stamps)


if __name__ == "__main__":
    emit_freshness("CALET", fetch_modified_at, label="CALET landmarks")
