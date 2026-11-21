#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Cockburn's street tree inventory.

The public layer's own `editingInfo.dataLastEditDate` (the City reloads it;
2026-11-20 when wired).  The asset-register copy the ingest reads for
`Tree_Status` is not probed: it has not been edited since February 2026, and
a change there without one here changes nothing worth a rebuild on its own.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, layer_last_edit
from shared.ingest import emit_freshness

# Kept in step with cockburn_tree_info.py's LAYER.
LAYER = FeatureLayer(
    "https://services-ap1.arcgis.com/pvAgeoXPDsVDgOQ0/arcgis/rest/services/"
    "Street_Trees_3857_1de94/FeatureServer/0"
)


def fetch_modified_at() -> datetime:
    return layer_last_edit(LAYER)


if __name__ == "__main__":
    emit_freshness("AUCKB", fetch_modified_at)
