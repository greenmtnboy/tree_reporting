#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""New Orleans, LA's Historic District Landmarks Commission landmarks.

Source: "HDLC Landmarks" (`Administrative/Historic_Preservation/MapServer/0`
on maps.nola.gov), described by the city as "updated based on
nomination/designation by HDLC": the register of individually designated
local landmarks of both commissions, the citywide HDLC and the CBD HDLC.
347 points on 2026-11-11, with an address, name, architect, construction
date, a link to the designation report and a per-row `EditDate`.  The older
polygon copy on gis.nola.gov (`apps/property3/MapServer/10`, 344 rows) has no
edit tracking and is not used.

**`nom_des` is the status.**  `Designated` (284) and `Nominated` (62) are
kept: a nominated property is already under the commission's review while its
designation is pending.  The status and the commission (`no_cbd`: `CBD`, or
`New Orleans`, sometimes misspelt `New Orelans`) travel in `protection_type`
(`HDLC designated`, `CBD HDLC nominated`), the column Paris and Prague use for
a protection status.  `Destroyed` (1, the Karnofsky Tailor Shop) is dropped;
any other status raises, because a new one is a schema change.

**69 landmarks have no name** (HDLC designates many houses by address alone),
so the address is the name there; every row has an address.  `Date_` is free
text (`1850`, `1840-53`, `c.1850's`, `Unknown`); its first plausible year is
`year_built`.

An official register on the city's own GIS is the runbook's first preference,
so there is no Nominatim geocoding, no committed CSV and nothing to stage.  The
layer has no landmark number, so `OBJECTID` is the key (the layer is edited in
place, as its per-row `EditDate` shows, rather than re-published).
"""

import re
import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_geometry_to_wkt, iter_features
from shared.ingest import emit

LAYER = FeatureLayer(
    "https://maps.nola.gov/server/rest/services/Administrative/Historic_Preservation/MapServer/0"
)

OUT_FIELDS = "OBJECTID,name,addr,nom_des,no_cbd,Date_"

KEPT_STATUSES = {"Designated": "designated", "Nominated": "nominated"}
DROPPED_STATUSES = frozenset({"Destroyed"})

_YEAR = re.compile(r"(1[6-9]\d\d|20[0-2]\d)")


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def status_of(value) -> str | None:
    """The kept status in lower case, None for a dropped one; raises on a new one."""
    text = clean(value) or ""
    if text in KEPT_STATUSES:
        return KEPT_STATUSES[text]
    if text in DROPPED_STATUSES:
        return None
    raise RuntimeError(f"New Orleans HDLC landmark status {text!r} is new; decide whether to keep it")


def commission_of(value) -> str:
    return "CBD HDLC" if (clean(value) or "").upper() == "CBD" else "HDLC"


def year_of(value) -> int | None:
    match = _YEAR.search(clean(value) or "")
    return int(match.group(1)) if match else None


def transform(features: list[dict]) -> pa.Table:
    landmark_id: list[str] = []
    name: list[str] = []
    geometry_raw: list[str] = []
    address: list[str | None] = []
    protection_type: list[str] = []
    year_built: list[int | None] = []

    for feature in features:
        attrs = feature.get("attributes") or {}
        status = status_of(attrs.get("nom_des"))
        if status is None:
            continue
        addr = clean(attrs.get("addr"))
        label = clean(attrs.get("name")) or addr
        wkt = esri_geometry_to_wkt(feature.get("geometry"))
        if attrs.get("OBJECTID") is None or not label or wkt is None:
            continue
        landmark_id.append(f"nol-{attrs['OBJECTID']}")
        name.append(label)
        geometry_raw.append(wkt)
        address.append(addr)
        protection_type.append(f"{commission_of(attrs.get('no_cbd'))} {status}")
        year_built.append(year_of(attrs.get("Date_")))

    return pa.table(
        {
            "landmark_id": pa.array(landmark_id, type=pa.string()),
            "name": pa.array(name, type=pa.string()),
            "city": pa.array(["USNOL"] * len(landmark_id), type=pa.string()),
            "geometry_raw": pa.array(geometry_raw, type=pa.string()),
            "address": pa.array(address, type=pa.string()),
            "protection_type": pa.array(protection_type, type=pa.string()),
            "year_built": pa.array(year_built, type=pa.int64()),
        }
    )


if __name__ == "__main__":
    features: list[dict] = []
    for page in iter_features(
        LAYER, out_fields=OUT_FIELDS, return_geometry=True, order_by="OBJECTID"
    ):
        features.extend(page)
    emit(transform(features))
