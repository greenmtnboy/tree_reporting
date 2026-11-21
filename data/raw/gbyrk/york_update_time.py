#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for York's tree inventory (council and private trees).

Both layers (`Public/EnvPlan/MapServer/3` and `/18`) are MapServer layers on
the council's on-prem ArcGIS Server: no `editingInfo` and no date column
(`Planted` is a year, on 1% of rows).  So this falls back to the catalogue:
the council's ArcGIS Hub (`data-cyc.opendata.arcgis.com`) lists both layers,
and the probe is the later of their DCAT `modified` stamps
(`hub_last_modified`, matched on the REST endpoint).

What that costs: it is the catalogue's stamp, not the data's.  It read
2023-11-07 for both on 2026-11-21 -- the day the council re-saved every Hub
item -- while the layers are "a live API link to CYC's GIS server", so the
stamp does **not** move when the council edits or reloads its trees.  The
city still rebuilds weekly: `gbyrk_published_data_updated_through` is the
greatest of this, the community probe and the OSM staging probe, and the
weekly OSM extract re-stamps its staging object every run (Vienna, `atvie/`,
is wired the same way).  The layer itself offers nothing that is a time.

A layer the Hub no longer lists, or an unreadable stamp, raises -- our wiring
or the catalogue changed shape -- and a transport failure degrades to the
epoch through `emit_freshness`.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import hub_last_modified
from shared.ingest import emit_freshness

HUB = "data-cyc.opendata.arcgis.com"
SERVICE = "https://maps.york.gov.uk/arcgis/rest/services/Public/EnvPlan/MapServer"
LAYERS = (f"{SERVICE}/3", f"{SERVICE}/18")


def fetch_modified_at() -> datetime:
    return max(hub_last_modified(HUB, layer) for layer in LAYERS)


if __name__ == "__main__":
    emit_freshness("GBYRK", fetch_modified_at)
