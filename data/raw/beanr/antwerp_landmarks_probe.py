#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Antwerp's landmarks (the Flemish heritage designations).

The Mercator layers publish no layer stamp and no edit-date column, so this
reads the latest designation in force for Antwerp: `MAX(geld_start)` over the
three layers `antwerp_landmarks.py` reads, filtered the same way, one row per
layer.  It moves when a new protection takes effect in Antwerp; a repeal (the
row leaves the layer) or a renamed object waits for the next new designation,
which for a register amended a few times a year in one city is acceptable.

A layer with no dated Antwerp row raises -- the register changing shape -- and
a transport failure degrades to the epoch through `emit_freshness`.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit_freshness, get_json_with_retry
from shared.platforms.wfs import WfsLayer, parse_wfs_datetime

sys.path.insert(0, str(Path(__file__).parent))
from antwerp_landmarks import ANTWERP_FILTER, BASE_URL, LAYERS  # noqa: E402


def latest_designation(type_name: str) -> datetime:
    layer = WfsLayer(BASE_URL, type_name, timeout=120)
    payload = get_json_with_retry(
        layer.base_url,
        params=layer.params(
            count=1,
            sortBy="geld_start D",
            propertyName="geld_start",
            cql_filter=f"{ANTWERP_FILTER} AND geld_start IS NOT NULL",
        ),
        timeout=layer.timeout,
    )
    features = payload.get("features") or []
    raw = (features[0].get("properties") or {}).get("geld_start") if features else None
    stamp = parse_wfs_datetime(raw)
    if stamp is None:
        raise RuntimeError(f"{type_name}: cannot read MAX(geld_start) for Antwerp ({raw!r})")
    return stamp


def fetch_modified_at() -> datetime:
    return max(latest_designation(type_name) for type_name, _ in LAYERS)


if __name__ == "__main__":
    emit_freshness("BEANR", fetch_modified_at, label="BEANR landmarks")
