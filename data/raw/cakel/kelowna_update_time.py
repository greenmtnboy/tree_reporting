#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Kelowna's municipal tree inventory.

**The maximum of two stamps, not a preference between them.**  The layer
publishes no `editingInfo`, so the obvious watermark is gone; what it does
carry is an `InventoryDate` per tree, whose maximum moves whenever a new
survey lands, and its Hub catalogue entry carries a `modified` stamp.  Neither
alone is trustworthy -- `InventoryDate` never moves for a correction to an
existing row, and the catalogue stamp can miss a service overwritten in place
-- so this takes the later of the two.

That is the rule `_ckan_shared.data_last_modified` arrived at the hard way:
preferring one stamp froze Toronto on its first build, because the one
preferred had stopped moving three years earlier.  A maximum cannot freeze a
city as long as any one of its inputs is alive.  Today the two disagree by
eight months (`InventoryDate` 2026-08, catalogue 2025-12), and the live one
wins.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from _arcgis_shared import FeatureLayer, field_max, hub_last_modified
from _ingest_shared import UpstreamUnavailable, emit_freshness

HUB_HOST = "opendata.kelowna.ca"
LAYER_URL = (
    "https://geoportal.kelowna.ca/arcgis/rest/services/ArcGISOnline/"
    "OpenData_Environment/MapServer/17"
)
LAYER = FeatureLayer(LAYER_URL)


def fetch_modified_at() -> datetime:
    """The later of the newest survey date and the catalogue's own stamp.

    Both are attempted and only an *availability* failure is tolerated, so a
    portal that is briefly down still yields the other stamp while a mapping
    that has gone wrong -- a renamed column, a dataset pulled from the
    catalogue -- still raises.  Degrading a schema error into "no new data"
    would freeze this city's parquet silently and for ever.
    """
    stamps: list[datetime] = []
    errors: list[Exception] = []
    for read in (lambda: field_max(LAYER, "InventoryDate"),
                 lambda: hub_last_modified(HUB_HOST, LAYER_URL)):
        try:
            stamps.append(read())
        except UpstreamUnavailable as exc:
            errors.append(exc)
    if not stamps:
        raise errors[0]
    return max(stamps)


if __name__ == "__main__":
    emit_freshness("CAKEL", fetch_modified_at)
