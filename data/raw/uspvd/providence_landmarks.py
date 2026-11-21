#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Providence, RI's National Register listings.

**The National Register of Historic Places** (the National Park Service's
`cultural_resources/nrhp_locations` MapServer, points then polygons) with
`City = Providence` and `State = RHODE ISLAND`, inside
`CITY_BOUNDS["USPVD"]` -- the box also holds East Providence, North
Providence, Cranston and Johnston listings, and Providence County is
the whole north of the state.  Listed buildings, sites and structures are
published, keyed `pvd-nr-{NRIS_Refnum}` with `CertDate` as `year_designated`;
districts are areas and are not published (Durham's rule), and neither is a
listing whose `STATUS` is not `Listed`.  NPS data is a US federal work.

The City's own designations are not read.  The Historic District Commission's
register is a set of local historic districts (areas) and a "Local Landmark
District" overlay published only as a PDF map (PVDGIS item
a9ac61847c424984b4216e355d6f1e91); the planning layers on the City's ArcGIS
Online organisation (`Federal_Historic_Districts_2016`, "Contributing and
Noncontributing Properties") are district polygons and unnamed parcels.
"""

import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_geometry_to_wkt, iter_features
from shared.ingest import CITY_BOUNDS, emit

NRHP = "https://mapservices.nps.gov/arcgis/rest/services/cultural_resources/nrhp_locations/MapServer"
NRHP_LAYERS = (FeatureLayer(f"{NRHP}/0"), FeatureLayer(f"{NRHP}/1"))  # points first
NRHP_FIELDS = "OBJECTID,NRIS_Refnum,RESNAME,ResType,Address,City,State,CertDate,STATUS"
NRHP_WHERE = "City = 'Providence' AND State = 'RHODE ISLAND'"

NATIONAL_TYPE = "National Register of Historic Places"

COLUMNS = (
    "landmark_id",
    "name",
    "geometry_raw",
    "address",
    "lm_type",
    "register_number",
    "year_designated",
)


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def bounding_box() -> dict[str, str]:
    lat_min, lat_max, lon_min, lon_max = CITY_BOUNDS["USPVD"]
    return {
        "geometry": f"{lon_min},{lat_min},{lon_max},{lat_max}",
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
    }


def build() -> pa.Table:
    rows: dict[str, list] = {k: [] for k in COLUMNS}
    seen: set[str] = set()
    skipped = {"district": 0, "not listed": 0}
    for layer in NRHP_LAYERS:
        for page in iter_features(
            layer,
            out_fields=NRHP_FIELDS,
            where=NRHP_WHERE,
            return_geometry=True,
            out_sr=4326,
            extra_params=bounding_box(),
        ):
            for feature in page:
                attrs = feature.get("attributes") or {}
                ref = clean(attrs.get("NRIS_Refnum"))
                name = clean(attrs.get("RESNAME"))
                wkt = esri_geometry_to_wkt(feature.get("geometry"))
                if not ref or not name or wkt is None or ref in seen:
                    continue
                if clean(attrs.get("STATUS")) != "Listed":
                    skipped["not listed"] += 1
                    continue
                if (clean(attrs.get("ResType")) or "").lower() == "district":
                    skipped["district"] += 1
                    continue
                seen.add(ref)
                cert = clean(attrs.get("CertDate")) or ""
                rows["landmark_id"].append(f"pvd-nr-{ref}")
                rows["name"].append(name)
                rows["geometry_raw"].append(wkt)
                rows["address"].append(clean(attrs.get("Address")))
                rows["lm_type"].append(NATIONAL_TYPE)
                rows["register_number"].append(ref)
                rows["year_designated"].append(int(cert[:4]) if cert[:4].isdigit() else None)
    if not seen:
        raise RuntimeError("no National Register listings read for Providence")
    print(
        f"Providence landmarks: {len(seen)} National Register listings published; skipped "
        f"{skipped['district']} districts and {skipped['not listed']} not listed",
        file=sys.stderr,
    )
    n = len(rows["landmark_id"])
    return pa.table(
        {
            "landmark_id": pa.array(rows["landmark_id"], type=pa.string()),
            "name": pa.array(rows["name"], type=pa.string()),
            "city": pa.array(["USPVD"] * n, type=pa.string()),
            "geometry_raw": pa.array(rows["geometry_raw"], type=pa.string()),
            "address": pa.array(rows["address"], type=pa.string()),
            "lm_type": pa.array(rows["lm_type"], type=pa.string()),
            "register_number": pa.array(rows["register_number"], type=pa.string()),
            "year_designated": pa.array(rows["year_designated"], type=pa.int64()),
        }
    )


if __name__ == "__main__":
    emit(build())
