#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Prague's tree register.

`MAX(AKTUALIZACE)`, the register's own per-row update stamp, read as one
statistics row from the same MapServer layer the ingest walks.  The layer
publishes no `editingInfo` (it is a MapServer republished from a file
geodatabase, not a hosted feature layer), and the Hub catalogue's
`modified` date belongs to the geometry-only copy, so the row stamp is the
one watermark that moves when the data does.  The suppliers' exports land
roughly monthly, so the city rebuilds on a few ticks a month of its
twice-weekly cron.

`field_max` raises when the column is missing or empty -- the register
changing shape, not an outage -- and lets a transport failure degrade to
the epoch through `emit_freshness`, so Prague sits out that run.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit_freshness
from shared.platforms.arcgis import FeatureLayer, field_max

# Kept in step with `prague_tree_info.py`.
LAYER = FeatureLayer(
    "https://gs-pub.praha.eu/arcgis/rest/services/opk/smz_vegetacni_prvky/MapServer/0"
)


def fetch_modified_at() -> datetime:
    return field_max(LAYER, "AKTUALIZACE")


if __name__ == "__main__":
    emit_freshness("CZPRG", fetch_modified_at)
