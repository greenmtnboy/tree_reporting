#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for York Region's street trees.

The layer (`OpenData/Biodiversity/MapServer/0` on the Region's own ArcGIS
Server, and its FeatureServer twin) publishes no `editingInfo` and carries no
date column (`YEARPLANTED` is a year), and the server's metadata pages hold
only the 2019 creation date.  So this falls back to the catalogue: the
Region's open data hub (`insights-york.opendata.arcgis.com`) lists the layer,
and its DCAT `modified` stamp is the watermark (`hub_last_modified`, matched
on the REST endpoint).

What that costs: it is the catalogue's stamp, not the data's.  It read
2025-08-01 on 2026-11-23 -- the day the Region re-saved its Hub items --
while the layer holds 2,373 trees planted in 2026, so the stamp does **not**
move when the Region edits its trees.  The city still rebuilds weekly:
`cayrk_published_data_updated_through` is the greatest of this, the community
probe and the OSM staging probe, and the weekly OSM extract re-stamps its
staging object every run (York UK, `gbyrk/`, and Vienna, `atvie/`, are wired
the same way).

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

HUB = "insights-york.opendata.arcgis.com"
LAYER_URL = (
    "https://ww8.yorkmaps.ca/arcgis/rest/services/OpenData/Biodiversity/MapServer/0"
)


def fetch_modified_at() -> datetime:
    return hub_last_modified(HUB, LAYER_URL)


if __name__ == "__main__":
    emit_freshness("CAYRK", fetch_modified_at)
