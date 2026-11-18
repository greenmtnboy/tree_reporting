#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Bern's landmarks (sights + Bauinventar ensembles).

The later of the two opendata.swiss records' `modified` dates (CKAN
`package_show` on ckan.opendata.swiss, the API host opendata.swiss redirects
to): `sehenswurdigkeiten` (2022-12-05 when this was written) and
`bauinventar1` (2016-12-21).  Neither ArcGIS layer has `editingInfo`, and the
Baugruppen layer has no date column; `metadata_modified` is not used, since
the geocat harvester moves it every night whether or not anything changed.

A missing or unparseable date raises (the catalogue changed shape); a
transport failure degrades to the epoch through `emit_freshness`.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit_freshness, get_json_with_retry

METADATA_URL = "https://ckan.opendata.swiss/api/3/action/package_show"
PACKAGES = ("sehenswurdigkeiten", "bauinventar1")


def package_modified(package: str) -> datetime:
    payload = get_json_with_retry(METADATA_URL, params={"id": package}, timeout=60)
    result = payload.get("result")
    if not isinstance(result, dict):
        raise RuntimeError(f"{package}: package_show returned no result")
    value = result.get("modified")
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"{package}: modified is missing ({value!r})")
    try:
        stamp = datetime.fromisoformat(value.strip())
    except ValueError as exc:
        raise RuntimeError(f"{package}: cannot read modified={value!r}") from exc
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def fetch_modified_at() -> datetime:
    return max(package_modified(p) for p in PACKAGES)


if __name__ == "__main__":
    emit_freshness("CHBRN", fetch_modified_at, label="CHBRN landmarks")
