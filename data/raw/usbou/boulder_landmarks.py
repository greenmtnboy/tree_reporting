#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Boulder, CO's individually designated historic landmarks.

Source: "Historic Landmarks", layer 0 of `plan/HistoricLandmarks/MapServer`
on gis.bouldercolorado.gov (ArcGIS item 6738cf5773fe46329464b0520621e915,
"Historical Districts and Landmarks", owner `BoulderCO`, licence CC0 1.0) --
the city's register of individual landmarks designated under its historic
preservation ordinance, one parcel polygon each.  230 rows on 2026-11-16,
every one `DESIGNATION = INDL` (Individual Landmark): 224 `CASESTATUS =
Approved` and 6 `Pending`.  Only approved designations are published; a
pending case is an application, not a landmark.

This is the runbook's first preference (an official designation register,
on the same server family as the trees), read live: no Nominatim, no CSV, no
staging object.  The layer is a polygon per landmark parcel in State Plane,
reprojected server-side to WGS84 and published as WKT; the template derives
the centroid.

The layer carries no register number and no GlobalID, so `OBJECTID` is the
key (`bou-{OBJECTID}`).  `SITENAME` is the name (`Squires-Tourtellot House`,
`Boulderado Hotel`), spelled as the city spells it (`Columbia Cemetary`).
Three approved rows have no name: two (OBJECTID 681 and 682, `0 CANYON
BLVD`) are named from the address (`Historic landmark, Canyon Blvd`), and
the third (609) has no address either and is skipped, so 223 are published.
`SITEADDRESS` is mixed-case and sometimes upper-case (`601 KALMIA AVE`,
`1125 Pine st`), so it is re-cased; a `0` house number (an unaddressed city
parcel) is dropped from the address.  `STYLE` (architectural style, through a
domain, filled on 88 rows) has no column in the landmark schema and is not
published.

The 32 historic districts (layer 1) are areas, not landmarks, and are not
published (Durham's and Denver's rule).  The National Register listings in
Boulder are not added either: most of them are also city landmarks, so they
would put two pins on one building.
"""

import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_geometry_to_wkt, iter_features
from shared.ingest import emit

LAYER = FeatureLayer(
    "https://gis.bouldercolorado.gov/ags_svr1/rest/services/plan/HistoricLandmarks/MapServer/0"
)

OUT_FIELDS = "OBJECTID,SITENAME,SITEADDRESS,DESIGNATION,CASESTATUS"

LM_TYPE = "City of Boulder individual landmark"

DIRECTIONS = frozenset({"N", "S", "E", "W", "NE", "NW", "SE", "SW"})


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def format_address(raw) -> str | None:
    """`601 KALMIA AVE` -> `601 Kalmia Ave`; `0 CANYON BLVD` -> `Canyon Blvd`."""
    text = clean(raw)
    if text is None:
        return None
    words = text.split()
    if words[0] == "0":
        words = words[1:]
    out = " ".join(
        w.lower() if w[0].isdigit() else (w.upper() if w.upper() in DIRECTIONS else w.capitalize())
        for w in words
    )
    return out or None


def build(features: list[dict]) -> pa.Table:
    landmark_id: list[str] = []
    name: list[str] = []
    geometry_raw: list[str] = []
    address: list[str | None] = []
    lm_type: list[str] = []

    for feature in sorted(features, key=lambda f: (f.get("attributes") or {}).get("OBJECTID") or 0):
        attrs = feature.get("attributes") or {}
        if clean(attrs.get("CASESTATUS")) != "Approved":
            continue
        oid = attrs.get("OBJECTID")
        wkt = esri_geometry_to_wkt(feature.get("geometry"))
        addr = format_address(attrs.get("SITEADDRESS"))
        label = clean(attrs.get("SITENAME")) or (f"Historic landmark, {addr}" if addr else None)
        if oid is None or wkt is None or label is None:
            continue
        landmark_id.append(f"bou-{oid}")
        name.append(label)
        geometry_raw.append(wkt)
        address.append(addr)
        lm_type.append(LM_TYPE)

    if not landmark_id:
        raise RuntimeError(f"{LAYER.base} returned no approved landmarks")
    n = len(landmark_id)
    return pa.table(
        {
            "landmark_id": pa.array(landmark_id, type=pa.string()),
            "name": pa.array(name, type=pa.string()),
            "city": pa.array(["USBOU"] * n, type=pa.string()),
            "geometry_raw": pa.array(geometry_raw, type=pa.string()),
            "address": pa.array(address, type=pa.string()),
            "lm_type": pa.array(lm_type, type=pa.string()),
        }
    )


if __name__ == "__main__":
    features = [
        feature
        for page in iter_features(
            LAYER, out_fields=OUT_FIELDS, return_geometry=True, out_sr=4326
        )
        for feature in page
    ]
    emit(build(features))
