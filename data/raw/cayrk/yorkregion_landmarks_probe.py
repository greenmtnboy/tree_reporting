#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Freshness probe for York Region's heritage properties.

Both views of the Region's hosted heritage layer publish `editingInfo`, and
it is the parent layer's (the two read the same `dataLastEditDate`,
2026-11-19 when the city was wired), a UTC instant; the later of the two is
the watermark.  An outage degrades; a view that stops publishing
`editingInfo` raises.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, layer_last_edit
from shared.ingest import emit_freshness

_SERVICES = "https://services1.arcgis.com/GzvOwaQBbX7KLiuG/arcgis/rest/services/"
LAYERS = (
    FeatureLayer(
        f"{_SERVICES}Heritage_Properties_Northern_Communities_/FeatureServer/0",
        timeout=60,
    ),
    FeatureLayer(
        f"{_SERVICES}Heritagen_Properties_Southern_Urban_Centres/FeatureServer/0",
        timeout=60,
    ),
)


def fetch_modified_at() -> datetime:
    return max(layer_last_edit(layer) for layer in LAYERS)


if __name__ == "__main__":
    emit_freshness("CAYRK", fetch_modified_at, label="CAYRK landmarks")
