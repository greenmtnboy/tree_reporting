#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Houston's tree inventory.  STUB -- point it at the portal.

Mandatory: without it the pipeline re-downloads the whole dataset on every
tick.  Hit the lightest metadata endpoint the portal exposes:

    ArcGIS     `shared.platforms.arcgis.layer_last_edit(LAYER)`, or `field_max` for a
               layer with no editingInfo
    OpenDataSoft  GET /api/explore/v2.1/catalog/datasets/{id} -> .metas.default.modified
    CKAN       GET /api/3/action/resource_show?id={id} -> .result.last_modified
    Socrata    `shared.platforms.socrata.rows_updated_at(DATASET)` -- reads
               /api/views/{id}.json .rowsUpdatedAt (unix SECONDS, not
               Esri's milliseconds)

Fetch with `get_json_with_retry`, never a bare `requests.get(...).json()`: a
portal in maintenance answers every path with an HTML holding page and HTTP
200, and every probe is a root datasource whose exception ends the whole
refresh for every city.  `emit_freshness` turns an availability failure into
the epoch so this city sits out the run; a *parse* failure still raises, because
that means the portal changed its schema and degrading would freeze this city's
Parquet silently and for ever.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit_freshness, get_json_with_retry

METADATA_URL = "TODO: the portal's metadata endpoint"


def fetch_modified_at() -> datetime:
    raise NotImplementedError("TODO: read Houston's published-through timestamp")


if __name__ == "__main__":
    emit_freshness("USHOU", fetch_modified_at)
