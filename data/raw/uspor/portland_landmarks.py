#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Portland, OR's designated landmarks and its National Register listings.

Three layers, all read live from ArcGIS:

* **Historic Landmarks** (`COP_OpenData_ZoningCode/MapServer/131` on
  portlandmaps.com, credited to the Bureau of Planning and Sustainability):
  the city's register of individually designated resources under Zoning Code
  chapter 33.445 -- buildings, structures, objects, sites and three trees.
  753 points on 2026-11-16.  Each row says whether it is a local landmark
  (`LOCAL_LDMK`) and whether it is individually listed on the National
  Register (`NR_LDMK`, with its NRIS reference in `nrNumber` and listing date
  in `NR_date_Listed`); `OVERSTATUS` = National Register Landmark marks the
  36 NR-only rows the city reviews under the limited rules.  The layer
  carries a `delete_record_flag` (a Yes/No domain) and an NR delisting date;
  a flagged or delisted row is not published (none is today).  `lm_type`
  names the register, or both.
* **Conservation Landmarks** (`COP_OpenData_ZoningCode/MapServer/133`, same
  publisher): 14 points of the lesser local designation.  Same schema.
* **The National Register of Historic Places** (NPS
  `cultural_resources/nrhp_locations`, points then polygons) inside
  `CITY_BOUNDS["USPOR"]` with `City = Portland` -- only the listings whose
  NRIS reference the city's layer does not already carry, so a building on
  both is one pin, not two.  Districts are areas and are not published
  (Durham's rule).  NPS data is a US federal work.

Keys: `pdx-hl-{LANDMARKID}` and `pdx-cl-{LANDMARKID}` for the city layers;
the city repeats a LANDMARKID for one resource mapped at two addresses
(`53`, the American Can Co. Complex), and those rows take
`pdx-hl-{LANDMARKID}-{OBJECTID}`.  NPS rows are `pdx-nr-{NRIS_Refnum}`.
`year_designated` is the National Register listing year where there is one
-- the city layer records no local designation date -- and is null for a
local-only landmark.  `YEARBUILT` (text, `1893`, `c.1910`) gives `year_built`.

Portland's Historic Resource Inventory (`COP_OpenData_PlanningDevelopment/
MapServer/132`, ~4,400 points from the 1984 survey) is not read: the layer's
own description says listing in it "is not a historic designation".
"""

import re
import sys
from collections import Counter
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import (
    FeatureLayer,
    esri_geometry_to_wkt,
    esri_ms_to_date,
    iter_features,
)
from shared.ingest import CITY_BOUNDS, emit, make_point_wkt

PDX = "https://www.portlandmaps.com/od/rest/services/COP_OpenData_ZoningCode/MapServer"
HISTORIC = FeatureLayer(f"{PDX}/131", timeout=120)
CONSERVATION = FeatureLayer(f"{PDX}/133", timeout=120)
CITY_FIELDS = (
    "OBJECTID,LANDMARKID,HISTNAME,COMMONNAME,ADDRESS,YEARBUILT,NR_LDMK,"
    "LOCAL_LDMK,nrNumber,NR_date_Listed,NR_date_Delisted,delete_record_flag"
)
CONSERVATION_FIELDS = (
    "OBJECTID,LANDMARKID,HISTNAME,COMMONNAME,ADDRESS,YEARBUILT,nrNumber,"
    "NR_date_Listed,NR_date_Delisted,delete_record_flag"
)

NRHP = "https://mapservices.nps.gov/arcgis/rest/services/cultural_resources/nrhp_locations/MapServer"
NRHP_LAYERS = (FeatureLayer(f"{NRHP}/0"), FeatureLayer(f"{NRHP}/1"))  # points first
NRHP_FIELDS = "OBJECTID,NRIS_Refnum,RESNAME,ResType,Address,City,State,CertDate,STATUS"

LOCAL_TYPE = "Portland Historic Landmark"
CONSERVATION_TYPE = "Portland Conservation Landmark"
NATIONAL_TYPE = "National Register of Historic Places"

# `delete_record_flag` domain code for Yes.
DELETED = 1

COLUMNS = (
    "landmark_id",
    "name",
    "geometry_raw",
    "address",
    "year_built",
    "lm_type",
    "register_number",
    "year_designated",
)

_YEAR = re.compile(r"(1[6-9]\d\d|20\d\d)")
_PLACEHOLDER_NAMES = frozenset({"", "---", "-", "none", "n/a"})


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return None if text.lower() in _PLACEHOLDER_NAMES else text


def year_of(value) -> int | None:
    match = _YEAR.search(str(value or ""))
    return int(match.group(1)) if match else None


def fetch_all(layer: FeatureLayer, **kwargs) -> list[dict]:
    return [feature for page in iter_features(layer, **kwargs) for feature in page]


def is_yes(value) -> bool:
    return (clean(value) or "").lower() == "yes"


def city_layer(layer: FeatureLayer, fields: str, prefix: str, conservation: bool):
    rows: dict[str, list] = {k: [] for k in COLUMNS}
    nris: set[str] = set()
    features = fetch_all(layer, out_fields=fields, return_geometry=True, out_sr=4326)
    if not features:
        raise RuntimeError(f"{layer.base} returned no landmarks")
    features.sort(key=lambda f: f["attributes"]["OBJECTID"])
    repeats = {
        k for k, n in Counter(
            (f["attributes"] or {}).get("LANDMARKID") for f in features
        ).items() if n > 1
    }
    for feature in features:
        attrs = feature.get("attributes") or {}
        geom = feature.get("geometry") or {}
        ref = clean(attrs.get("nrNumber"))
        if ref:
            # Even a row not published here keeps its NRIS number out of the
            # NPS pass: the city has decided that resource already.
            nris.add(ref)
        if attrs.get("delete_record_flag") == DELETED:
            continue
        if attrs.get("NR_date_Delisted") is not None:
            continue
        name = clean(attrs.get("HISTNAME")) or clean(attrs.get("COMMONNAME"))
        landmark = attrs.get("LANDMARKID")
        wkt = make_point_wkt(geom.get("x"), geom.get("y"))
        if not name or landmark is None or wkt is None:
            continue
        if conservation:
            lm_type = CONSERVATION_TYPE
        else:
            registers = []
            if is_yes(attrs.get("LOCAL_LDMK")):
                registers.append(LOCAL_TYPE)
            if is_yes(attrs.get("NR_LDMK")):
                registers.append(NATIONAL_TYPE)
            if not registers:
                raise RuntimeError(
                    f"Portland landmark {landmark} ({name}) is on neither register; "
                    "the layer's flags have changed"
                )
            lm_type = "; ".join(registers)
        key = f"{prefix}-{landmark}"
        if landmark in repeats:
            key = f"{key}-{attrs['OBJECTID']}"
        listed = esri_ms_to_date(attrs.get("NR_date_Listed"))
        rows["landmark_id"].append(key)
        rows["name"].append(name)
        rows["geometry_raw"].append(wkt)
        rows["address"].append(clean(attrs.get("ADDRESS")))
        rows["year_built"].append(year_of(attrs.get("YEARBUILT")))
        rows["lm_type"].append(lm_type)
        rows["register_number"].append(ref)
        rows["year_designated"].append(listed.year if listed else None)
    return rows, nris


def national_register(already: set[str]) -> dict[str, list]:
    lat_min, lat_max, lon_min, lon_max = CITY_BOUNDS["USPOR"]
    box = {
        "geometry": f"{lon_min},{lat_min},{lon_max},{lat_max}",
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
    }
    seen: set[str] = set()
    rows: dict[str, list] = {k: [] for k in COLUMNS}
    for layer in NRHP_LAYERS:
        for feature in fetch_all(
            layer, out_fields=NRHP_FIELDS, return_geometry=True, out_sr=4326, extra_params=box
        ):
            attrs = feature.get("attributes") or {}
            ref = clean(attrs.get("NRIS_Refnum"))
            name = clean(attrs.get("RESNAME"))
            wkt = esri_geometry_to_wkt(feature.get("geometry"))
            if not ref or not name or wkt is None or ref in seen or ref in already:
                continue
            if (clean(attrs.get("City")) or "").lower() != "portland":
                continue
            if (clean(attrs.get("State")) or "").upper() != "OREGON":
                continue
            if clean(attrs.get("STATUS")) != "Listed":
                continue
            if (clean(attrs.get("ResType")) or "").lower() == "district":
                continue
            seen.add(ref)
            cert = clean(attrs.get("CertDate")) or ""
            rows["landmark_id"].append(f"pdx-nr-{ref}")
            rows["name"].append(name)
            rows["geometry_raw"].append(wkt)
            rows["address"].append(clean(attrs.get("Address")))
            rows["year_built"].append(None)
            rows["lm_type"].append(NATIONAL_TYPE)
            rows["register_number"].append(ref)
            rows["year_designated"].append(int(cert[:4]) if cert[:4].isdigit() else None)
    return rows


def build() -> pa.Table:
    historic, nris = city_layer(HISTORIC, CITY_FIELDS, "pdx-hl", conservation=False)
    conservation, more = city_layer(
        CONSERVATION, CONSERVATION_FIELDS, "pdx-cl", conservation=True
    )
    national = national_register(nris | more)
    data = {k: historic[k] + conservation[k] + national[k] for k in COLUMNS}
    n = len(data["landmark_id"])
    print(
        f"Portland landmarks: {len(historic['landmark_id'])} historic, "
        f"{len(conservation['landmark_id'])} conservation, "
        f"{len(national['landmark_id'])} National Register only",
        file=sys.stderr,
    )
    return pa.table(
        {
            "landmark_id": pa.array(data["landmark_id"], type=pa.string()),
            "name": pa.array(data["name"], type=pa.string()),
            "city": pa.array(["USPOR"] * n, type=pa.string()),
            "geometry_raw": pa.array(data["geometry_raw"], type=pa.string()),
            "address": pa.array(data["address"], type=pa.string()),
            "year_built": pa.array(data["year_built"], type=pa.int64()),
            "lm_type": pa.array(data["lm_type"], type=pa.string()),
            "register_number": pa.array(data["register_number"], type=pa.string()),
            "year_designated": pa.array(data["year_designated"], type=pa.int64()),
        }
    )


if __name__ == "__main__":
    emit(build())
