#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Houston, TX's designated historic landmarks.

Source: "Houston Historic Sites and Landmarks", the City's hosted view
`COH_Historic_Sites_and_Landmarks_view/FeatureServer/6` on its ArcGIS Online
organisation (owner `cohgis_ago`): the Planning & Development Department's
register of City of Houston Landmarks and Protected Landmarks, the same 422
points as the department's `HoustonMap/Planning_and_Development/MapServer/7`
on mycity2.houstontx.gov (checked row for row when the city was wired).  The
hosted view is read because it publishes `editingInfo`, which the on-prem
MapServer does not, so the probe has a real edit stamp.

**A row is an application, and `STATUS` says how it ended.**  A null status
(357 rows) and `A` (Approved, 37) are designated landmarks and are kept.
`P` Pending (9), `H` On hold (7), `W` Withdrawn (6), `D` Demolished (5) and
`X` Issue with application (1) are not on the register and are dropped; any
other code raises, because a new status is a decision about which side of the
line it falls on.

**The register repeats itself.**  Twelve approved landmarks of the 2014
batch (`14L290`-`14L301`) and two Protected Landmarks appear twice, as rows
identical in every column read here and at the same point.  Duplicates are
collapsed on (name, address, position), keeping the lower `OBJECTID`.  Three
rows with no site name are dropped.  That leaves 379 landmarks.

`OBJECTID` is the key.  `LM_NUM`, the landmark file number, is not unique
(`14L294` names two different buildings), is `-` or null on 89 rows, and
does not exist for most Protected Landmarks.

The register's owner, agent and contact columns are never requested.
`DESIG_TYPE` (`LM` Landmark, `PLM` Protected Landmark) maps onto `lm_type`;
`designation_date` is the Protected Landmark designation date for a PLM and
the Landmark date otherwise, either one when only one is recorded;
`year_built` is the first four-digit year in `YR_BUILT1` (`c 1910`,
`1879-1880`), null for `?`.
"""

import re
import sys
from datetime import date
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_ms_to_date, esri_point, iter_features
from shared.ingest import emit, make_point_wkt

LAYER = FeatureLayer(
    "https://services.arcgis.com/NummVBqZSIJKUeVR/arcgis/rest/services/"
    "COH_Historic_Sites_and_Landmarks_view/FeatureServer/6"
)

OUT_FIELDS = (
    "OBJECTID,STATUS,DESIG_TYPE,SITE_NAME,SITE_ADDRESS,NEIGHBORHOOD,"
    "YR_BUILT1,LM_DESIG_DATE,PLM_DESIG_DATE"
)

DESIGNATED_STATUSES = frozenset({"", "A"})
NOT_DESIGNATED_STATUSES = frozenset({"P", "H", "W", "D", "X"})

LM_TYPES = {"LM": "Landmark", "PLM": "Protected Landmark"}

NEIGHBORHOODS = {"OSW": "Old Sixth Ward", "Warehouse Dist": "Warehouse District"}

YEAR = re.compile(r"\b(1[6-9]\d\d|20\d\d)\b")


def is_designated(status: str | None) -> bool:
    """True for a designated landmark; raises on a status not seen before."""
    value = (status or "").strip()
    if value in DESIGNATED_STATUSES:
        return True
    if value in NOT_DESIGNATED_STATUSES:
        return False
    raise RuntimeError(
        f"Houston landmark STATUS {value!r} is new; decide whether it is designated"
    )


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def year_built_of(value) -> int | None:
    match = YEAR.search(clean(value) or "")
    return int(match.group(1)) if match else None


def designation_date_of(attrs: dict) -> date | None:
    lm = esri_ms_to_date(attrs.get("LM_DESIG_DATE"))
    plm = esri_ms_to_date(attrs.get("PLM_DESIG_DATE"))
    if clean(attrs.get("DESIG_TYPE")) == "PLM":
        return plm or lm
    return lm or plm


def transform(features: list[dict]) -> pa.Table:
    landmark_id: list[str] = []
    name: list[str] = []
    geometry_raw: list[str] = []
    address: list[str | None] = []
    lm_type: list[str | None] = []
    neighborhood: list[str | None] = []
    year_built: list[int | None] = []
    designation_date: list[date | None] = []

    seen: set[tuple] = set()
    for feature in sorted(features, key=lambda f: (f.get("attributes") or {}).get("OBJECTID") or 0):
        attrs = feature.get("attributes") or {}
        if not is_designated(attrs.get("STATUS")):
            continue
        label = clean(attrs.get("SITE_NAME"))
        lat, lon = esri_point(feature.get("geometry"))
        wkt = make_point_wkt(lon, lat)
        if attrs.get("OBJECTID") is None or not label or wkt is None:
            continue
        street = clean(attrs.get("SITE_ADDRESS"))
        identity = (label, street, round(lon, 6), round(lat, 6))
        if identity in seen:
            continue
        seen.add(identity)
        landmark_id.append(f"hou-{attrs['OBJECTID']}")
        name.append(label)
        geometry_raw.append(wkt)
        address.append(street)
        lm_type.append(LM_TYPES.get(clean(attrs.get("DESIG_TYPE")) or ""))
        hood = clean(attrs.get("NEIGHBORHOOD"))
        neighborhood.append(NEIGHBORHOODS.get(hood, hood) if hood else None)
        year_built.append(year_built_of(attrs.get("YR_BUILT1")))
        designation_date.append(designation_date_of(attrs))

    return pa.table(
        {
            "landmark_id": pa.array(landmark_id, type=pa.string()),
            "name": pa.array(name, type=pa.string()),
            "city": pa.array(["USHOU"] * len(landmark_id), type=pa.string()),
            "geometry_raw": pa.array(geometry_raw, type=pa.string()),
            "address": pa.array(address, type=pa.string()),
            "lm_type": pa.array(lm_type, type=pa.string()),
            "neighborhood": pa.array(neighborhood, type=pa.string()),
            "year_built": pa.array(year_built, type=pa.int64()),
            "designation_date": pa.array(designation_date, type=pa.date32()),
        }
    )


if __name__ == "__main__":
    features: list[dict] = []
    for page in iter_features(
        LAYER, out_fields=OUT_FIELDS, return_geometry=True, order_by="OBJECTID"
    ):
        features.extend(page)
    emit(transform(features))
