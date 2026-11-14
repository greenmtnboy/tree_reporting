#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Houston's street-tree inventory.

The layer is served from an on-prem ArcGIS Server that publishes no
`editingInfo`, its schema has no edit-date column for `field_max`, and the
City's Hub site publishes no DCAT feed for `hub_last_modified`.  What it does
carry is the stamp the City's publishing script appends to the layer
description, "This layer was last updated: 2022-07-22 13:44:30.782122.",
which read 2022-07-22 when the city was wired (the survey itself says "Last
updated May 2019").  That stamp is layer-specific, unlike the map service's
"Published on" item stamp, which moves whenever any of the service's eighteen
layers (parcels among them) is republished.

A description without the stamp raises: the publisher changed how it reports
the layer, and degrading to "no new data" would freeze the city silently.
"""

import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, layer_metadata
from shared.ingest import emit_freshness

LAYER = FeatureLayer(
    "https://geohwp.houstontx.gov/arcgis/rest/services/"
    "03_BaseData_External/Land_Base/FeatureServer/0"
)

LAST_UPDATED = re.compile(
    r"This layer was last updated:\s*(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})"
)


def fetch_modified_at() -> datetime:
    description = layer_metadata(LAYER).get("description") or ""
    match = LAST_UPDATED.search(description)
    if match is None:
        raise RuntimeError(
            f"Houston Trees layer description no longer carries its "
            f"'last updated' stamp: {LAYER.base}"
        )
    return datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S").replace(
        tzinfo=timezone.utc
    )


if __name__ == "__main__":
    emit_freshness("USHOU", fetch_modified_at)
