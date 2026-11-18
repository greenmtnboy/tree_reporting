#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Eindhoven's monument registers (gemeentelijke and rijksmonumenten).

The later of two stamps, each one small request:

* `metas.default.modified` of the city's `monumenten` record on
  data.eindhoven.nl (ODS writes it with a `+00:00` offset);
* the largest `versionid` (an ISO timestamp per register entry) among RCE's
  Protected Sites points in the city's box on PDOK, read as one feature
  sorted descending.  The box keeps a mutation elsewhere in the country
  (the national register changes daily) from rebuilding Eindhoven.

A missing field raises (a register changed shape); a transport failure
degrades to the epoch through `get_json_with_retry` / `emit_freshness`.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit_freshness, get_json_with_retry

CITY_METADATA_URL = "https://data.eindhoven.nl/api/explore/v2.1/catalog/datasets/monumenten"
RCE_WFS = "https://service.pdok.nl/rce/ps-ch/wfs/v1_0"
BBOX = (51.39, 5.35, 51.51, 5.56)


def _parse(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(timezone.utc)


def city_modified_at() -> datetime:
    meta = get_json_with_retry(CITY_METADATA_URL)
    stamp = ((meta.get("metas") or {}).get("default") or {}).get("modified")
    if not stamp:
        raise RuntimeError("data.eindhoven.nl monumenten: metas.default.modified is missing")
    return _parse(stamp)


def rce_modified_at() -> datetime:
    lat0, lon0, lat1, lon1 = BBOX
    payload = get_json_with_retry(
        RCE_WFS,
        params={
            "service": "WFS",
            "version": "2.0.0",
            "request": "GetFeature",
            "typeNames": "ps-ch:rce_inspire_points",
            "outputFormat": "application/json",
            "srsName": "EPSG:4326",
            "count": 1,
            "sortBy": "versionid DESC",
            "propertyName": "versionid",
            "bbox": f"{lat0},{lon0},{lat1},{lon1},urn:ogc:def:crs:EPSG::4326",
        },
        timeout=60,
    )
    features = payload.get("features") or []
    stamp = (features[0].get("properties") or {}).get("versionid") if features else None
    if not stamp:
        raise RuntimeError("PDOK RCE rce_inspire_points: MAX(versionid) came back empty")
    return _parse(stamp)


def fetch_modified_at() -> datetime:
    return max(city_modified_at(), rce_modified_at())


if __name__ == "__main__":
    emit_freshness("NLEIN", fetch_modified_at, label="NLEIN landmarks")
