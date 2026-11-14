#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Freshness probe for Charlotte's historic landmark register.

**The maximum of two stamps.**  Mecklenburg County's layer publishes no
`editingInfo`; what it carries is `desigdate`, whose maximum moves when the
Historic Landmarks Commission designates a new property, and the
data.charlottenc.gov Hub entry ("Historic Properties Local") carries a
`modified` stamp.  Neither alone is trustworthy -- a designation date misses
an edit to an existing entry, and the catalogue stamp can miss a service
overwritten in place -- so the later of the two wins, as for Lethbridge.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, field_max, hub_last_modified
from shared.ingest import UpstreamUnavailable, emit_freshness

HUB_HOST = "data.charlottenc.gov"
LAYER_URL = (
    "https://meckgis.mecklenburgcountync.gov/server/rest/services/"
    "LocalHistoricProperty/FeatureServer/0"
)
LAYER = FeatureLayer(LAYER_URL)


def fetch_modified_at() -> datetime:
    """The later of the newest designation and the catalogue's own stamp."""
    stamps: list[datetime] = []
    errors: list[Exception] = []
    for read in (lambda: field_max(LAYER, "desigdate"),
                 lambda: hub_last_modified(HUB_HOST, LAYER_URL)):
        try:
            stamps.append(read())
        except UpstreamUnavailable as exc:
            errors.append(exc)
    if not stamps:
        raise errors[0]
    return max(stamps)


if __name__ == "__main__":
    emit_freshness("USCLT", fetch_modified_at, label="USCLT landmarks")
