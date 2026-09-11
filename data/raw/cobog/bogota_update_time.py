#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Bogotá's urban tree census.

The layer publishes no `editingInfo`, so the watermark is `MAX(Fecha_Actualizacion)`
-- the census's own per-row update stamp, read with one statistics query
rather than the 1.39M rows.  SIGAU updates the layer in place, and a row's
stamp moves when a crew re-surveys the tree, so this is the honest "the data
changed" signal: the catalogue entry's `metadata_modified` moves for a
description edit as well.

`field_max` raises when the statistics row is missing or empty -- that is the
layer changing shape, not the portal being down -- and `emit_freshness` lets a
genuine outage degrade to the epoch so Bogotá sits out the run.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from _arcgis_shared import FeatureLayer, field_max
from _ingest_shared import emit_freshness

# Kept in step with `bogota_tree_info.py` by `tests/test_city_wiring.py`.
LAYER = FeatureLayer(
    "https://geoportal.jbb.gov.co/agc/rest/services/IDECA/CensoArbol/FeatureServer/0",
    timeout=180,
)


def fetch_modified_at() -> datetime:
    return field_max(LAYER, "Fecha_Actualizacion")


if __name__ == "__main__":
    emit_freshness("COBOG", fetch_modified_at)
