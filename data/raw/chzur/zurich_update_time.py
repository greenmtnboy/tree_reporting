#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Zürich's tree cadastre.

The catalogue's own `dateLastUpdated` for the `geo_baumkataster` package on
data.stadt-zuerich.ch (CKAN), a `DD.MM.YYYY` date the Geoportal stamps when
it republishes the data -- `30.08.2026` when this was written, against an
advertised weekly interval.  The obvious CKAN stamps are not the watermark:
`metadata_modified` moves every morning when the Geoportal harvester re-runs
whether or not anything changed (it would rebuild the city daily), and the
resources carry no `last_modified` at all.  The WFS itself (QGIS Server)
publishes no layer stamp, and the layer has no edit-date column to take a
maximum of.

The date is read as midnight UTC.  A missing or unparseable value raises --
the catalogue changing shape must not freeze the city -- while a transport
failure degrades to the epoch through `emit_freshness`.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit_freshness, get_json_with_retry

METADATA_URL = "https://data.stadt-zuerich.ch/api/3/action/package_show"
PACKAGE_ID = "geo_baumkataster"


def parse_swiss_date(value) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"{PACKAGE_ID}: dateLastUpdated is missing ({value!r})")
    try:
        day = datetime.strptime(value.strip(), "%d.%m.%Y")
    except ValueError as exc:
        raise RuntimeError(f"{PACKAGE_ID}: cannot read dateLastUpdated={value!r}") from exc
    return day.replace(tzinfo=timezone.utc)


def fetch_modified_at() -> datetime:
    payload = get_json_with_retry(METADATA_URL, params={"id": PACKAGE_ID}, timeout=60)
    result = payload.get("result")
    if not isinstance(result, dict):
        raise RuntimeError(f"{PACKAGE_ID}: package_show returned no result")
    return parse_swiss_date(result.get("dateLastUpdated"))


if __name__ == "__main__":
    emit_freshness("CHZUR", fetch_modified_at)
