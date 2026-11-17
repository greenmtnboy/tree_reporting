#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Frankfurt's natural-monument landmarks.

Neither WFS (ArcGIS Server) publishes an edit date, and the GovData records
that catalogue them are re-harvested daily, so their `modified` moves whether
or not anything changed.  What does move with the register is its own
designation date: the latest `Verordnungsdatum` (natural monuments; the
ordinance re-designating all 38 is dated 29.10.2025) and `Datum` (protected
landscape features), `DD.MM.YYYY`, read as midnight UTC.  A new or re-issued
designation advances it; a redrawn polygon does not, which is the cost.

The layers are tiny (55 features), so the probe reads them whole: asking
for the date alone (`PROPERTYNAME`) makes the server emit invalid GeoJSON
(`"geometry":{"type":"MultiPolygon",}`).  An unreadable date, or none at all,
raises (the layer changed shape); a transport failure degrades to the epoch
through `emit_freshness`.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit_freshness, get_json_with_retry

# (service URL, feature type, date property)
LAYERS = (
    (
        "https://geowebdienste.frankfurt.de/WFS_Naturdenkmale",
        "Naturdenkmale:Naturdenkmale",
        "Verordnungsdatum",
    ),
    (
        "https://geowebdienste.frankfurt.de/WFS_Geschuetzte_Landschaftsbestandteile",
        "Geschuetzte_Landschaftsbestandteile:Geschuetzte_Landschaftsbestandteile",
        "Datum",
    ),
)


def layer_latest(url: str, type_name: str, field: str) -> datetime:
    payload = get_json_with_retry(
        url,
        params={
            "SERVICE": "WFS",
            "VERSION": "2.0.0",
            "REQUEST": "GetFeature",
            "TYPENAMES": type_name,
            "outputFormat": "GEOJSON",
        },
        timeout=60,
    )
    features = payload.get("features") if isinstance(payload, dict) else None
    if not features:
        raise RuntimeError(f"Frankfurt WFS: {type_name} returned no features")
    stamps: list[datetime] = []
    for feature in features:
        value = (feature.get("properties") or {}).get(field)
        if value is None or not str(value).strip():
            continue  # an undated row says nothing about the others
        try:
            day = datetime.strptime(str(value).strip(), "%d.%m.%Y")
        except ValueError as exc:
            raise RuntimeError(f"Frankfurt WFS: {type_name} {field}={value!r} is unreadable") from exc
        stamps.append(day.replace(tzinfo=timezone.utc))
    if not stamps:
        raise RuntimeError(f"Frankfurt WFS: {type_name} has no {field} on any row")
    return max(stamps)


def fetch_modified_at() -> datetime:
    return max(layer_latest(url, type_name, field) for url, type_name, field in LAYERS)


if __name__ == "__main__":
    emit_freshness("DEFRA", fetch_modified_at, label="DEFRA landmarks")
