#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Orland Park, Illinois's tree inventory.

The layer (`Public_Trees_6-28-26/FeatureServer/1`) is a view over the
Village's working layer, edited in place (its OBJECTIDs run 1..44,900 for
36,717 rows, the gaps deleted trees leave), and the view's own
`editingInfo.dataLastEditDate` follows the edits (2026-11-19 when the city
was wired).

The service name is dated, and this probe hard-codes it: the Village made
this view on 2026-06-28 over a layer made on 2025-12-23, and may publish a
new dated view and retire this one.  A retired service would answer with an
ArcGIS error envelope, which degrades as an outage and would freeze the city
silently, so before reading the stamp the probe asks ArcGIS Online which
public "Public Trees" feature services the Village's organisation has, and
raises if ours is no longer among them or another one has appeared: the
ingest and this probe then need pointing at the new service.  A search or
layer outage degrades through `get_json_with_retry`; a layer that stops
publishing `editingInfo` raises (`layer_last_edit`).
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, layer_last_edit  # noqa: E402
from shared.ingest import emit_freshness, get_json_with_retry  # noqa: E402

LAYER = FeatureLayer(
    "https://services3.arcgis.com/sVDuYsm37AMvFL49/arcgis/rest/services/"
    "Public_Trees_6-28-26/FeatureServer/1",
    timeout=60,
)
ITEM_ID = "780d9c0a3883498b82e046ef231144c9"
ORG_ID = "sVDuYsm37AMvFL49"
SEARCH_URL = "https://www.arcgis.com/sharing/rest/search"


def check_service_is_current() -> None:
    payload = get_json_with_retry(
        SEARCH_URL,
        params={
            "q": f'orgid:{ORG_ID} type:"Feature Service" title:"Public Trees"',
            "num": 50,
            "f": "json",
        },
        timeout=60,
    )
    if "results" not in payload:
        raise RuntimeError(f"ArcGIS Online search answered without results: {payload!r}"[:500])
    items = {r.get("id"): r.get("title") for r in payload["results"]}
    if ITEM_ID not in items:
        raise RuntimeError(
            f"Orland Park's tree service (item {ITEM_ID}) is no longer published; "
            f"the Village's 'Public Trees' services are now {items}"
        )
    others = {k: v for k, v in items.items() if k != ITEM_ID}
    if others:
        raise RuntimeError(
            f"Orland Park published another 'Public Trees' service {others}; "
            "check whether it replaces Public_Trees_6-28-26 and repoint the ingest"
        )


def fetch_modified_at() -> datetime:
    check_service_is_current()
    return layer_last_edit(LAYER)


if __name__ == "__main__":
    emit_freshness("USOPK", fetch_modified_at)
