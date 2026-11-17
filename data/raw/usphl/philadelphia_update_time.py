#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Philadelphia's tree inventory.

PPR publishes each year's snapshot as a new hosted layer
(`ppr_tree_inventory_{year}`), so the probe first finds the newest year the
way the ingest does (`latest_layer`: the highest year in the organisation's
services directory, its fields checked) and then reads that layer's
`editingInfo.dataLastEditDate` (2026-11-03 for the 2026 layer when the city
was wired).  A new year's layer is a later stamp than the last one, and an
edit to the current year's layer moves it too.

The discovery is repeated here rather than imported from the ingest: a probe
runs as its own script in the job's bundle, and the ten lines are not worth a
shared module for one city.  An outage degrades through
`get_json_with_retry`; a services directory with no inventory, or a layer
missing `editingInfo` or the ingest's fields, raises.
"""

import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, layer_last_edit, layer_metadata
from shared.ingest import emit_freshness, get_json_with_retry

SERVICES_URL = "https://services.arcgis.com/fLeGjb7u4uXqeF9q/arcgis/rest/services"
LAYER_NAME = re.compile(r"ppr_tree_inventory_(\d{4})", re.IGNORECASE)
EXPECTED_FIELDS = frozenset({"objectid", "tree_name", "tree_dbh", "loc_y", "loc_x"})


def latest_layer() -> FeatureLayer:
    payload = get_json_with_retry(SERVICES_URL, params={"f": "json"}, timeout=120)
    years = {
        int(m.group(1)): service["name"]
        for service in payload.get("services") or []
        if service.get("type") == "FeatureServer"
        and (m := LAYER_NAME.fullmatch(service.get("name") or ""))
    }
    if not years:
        raise RuntimeError(f"no ppr_tree_inventory_{{year}} service in {SERVICES_URL}")
    name = years[max(years)]
    layer = FeatureLayer(f"{SERVICES_URL}/{name}/FeatureServer/0", timeout=60)
    fields = {f["name"].lower() for f in layer_metadata(layer).get("fields") or []}
    missing = EXPECTED_FIELDS - fields
    if missing:
        raise RuntimeError(f"Philadelphia {name} lacks {sorted(missing)}; the schema changed")
    return layer


def fetch_modified_at() -> datetime:
    return layer_last_edit(latest_layer())


if __name__ == "__main__":
    emit_freshness("USPHL", fetch_modified_at)
