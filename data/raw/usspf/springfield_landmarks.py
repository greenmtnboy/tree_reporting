#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Springfield, MO's National Register of Historic Places listings.

**The National Register of Historic Places** (the National Park Service's
`cultural_resources/nrhp_locations` MapServer, points then polygons) within
`CITY_BOUNDS["USSPF"]` and with `City = Springfield` (one listing is filed as
"Springfiled" and is accepted too).  Listed buildings, sites, structures and
objects, keyed `springfield-nr-{NRIS_Refnum}` with `CertDate` as
`year_designated`; districts are areas and are not published (Columbus's and
Durham's rule).  NPS data is a US federal work.

**The city publishes no landmark register as data.**  Its ArcGIS Online org
(`cosmo`) holds a "Landmarks Board" service whose three layers are Greene
County assessor parcels (owner names and valuations, no designation), a
"Commercial Street National Historic District Boundaries" polygon (a
district, already in the NRHP) and a "Route 66 Historic Sites" web map; none
is a register of designated local landmarks.
"""

import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_geometry_to_wkt, iter_features
from shared.ingest import CITY_BOUNDS, emit

NRHP = "https://mapservices.nps.gov/arcgis/rest/services/cultural_resources/nrhp_locations/MapServer"
NRHP_LAYERS = (FeatureLayer(f"{NRHP}/0"), FeatureLayer(f"{NRHP}/1"))  # points first
NRHP_FIELDS = "OBJECTID,NRIS_Refnum,RESNAME,ResType,Address,City,CertDate,STATUS"

NATIONAL_TYPE = "National Register of Historic Places"

# The NRHP `City` values that are this Springfield (one row has the typo).
CITY_NAMES = frozenset({"springfield", "springfiled"})

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


def national_register() -> dict[str, list]:
    lat_min, lat_max, lon_min, lon_max = CITY_BOUNDS["USSPF"]
    box = {
        "geometry": f"{lon_min},{lat_min},{lon_max},{lat_max}",
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
    }
    seen: set[str] = set()
    rows: dict[str, list] = {k: [] for k in COLUMNS}
    for layer in NRHP_LAYERS:
        for page in iter_features(
            layer, out_fields=NRHP_FIELDS, return_geometry=True, out_sr=4326, extra_params=box
        ):
            for feature in page:
                attrs = feature.get("attributes") or {}
                ref = clean(attrs.get("NRIS_Refnum"))
                name = clean(attrs.get("RESNAME"))
                wkt = esri_geometry_to_wkt(feature.get("geometry"))
                if not ref or not name or wkt is None or ref in seen:
                    continue
                if (clean(attrs.get("City")) or "").lower() not in CITY_NAMES:
                    continue
                if clean(attrs.get("STATUS")) != "Listed":
                    continue
                if (clean(attrs.get("ResType")) or "").lower() == "district":
                    continue
                seen.add(ref)
                cert = clean(attrs.get("CertDate")) or ""
                rows["landmark_id"].append(f"springfield-nr-{ref}")
                rows["name"].append(name)
                rows["geometry_raw"].append(wkt)
                rows["address"].append(clean(attrs.get("Address")))
                rows["lm_type"].append(NATIONAL_TYPE)
                rows["register_number"].append(ref)
                rows["year_designated"].append(int(cert[:4]) if cert[:4].isdigit() else None)
    if not rows["landmark_id"]:
        raise RuntimeError("no National Register listings inside Springfield's bounds")
    return rows


def build() -> pa.Table:
    data = national_register()
    n = len(data["landmark_id"])
    return pa.table(
        {
            "landmark_id": pa.array(data["landmark_id"], type=pa.string()),
            "name": pa.array(data["name"], type=pa.string()),
            "city": pa.array(["USSPF"] * n, type=pa.string()),
            "geometry_raw": pa.array(data["geometry_raw"], type=pa.string()),
            "address": pa.array(data["address"], type=pa.string()),
            "lm_type": pa.array(data["lm_type"], type=pa.string()),
            "register_number": pa.array(data["register_number"], type=pa.string()),
            "year_designated": pa.array(data["year_designated"], type=pa.int64()),
        }
    )


if __name__ == "__main__":
    emit(build())
