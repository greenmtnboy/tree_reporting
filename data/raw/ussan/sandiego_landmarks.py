#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""San Diego, CA's designated historical resources and National Register listings.

Two registers, both read live from ArcGIS layers:

* **The City of San Diego's designated historical resources**, the
  Historical Resources Board (HRB) register: `HRB_DESIGNATED_RESOURCES`,
  layer 0 of `Planning/Historic_Preservation_Resources/MapServer` on
  webmaps.sandiego.gov, the City's own GIS server (the same server as the
  trees).  3,531 points on 2026-11-18: 1,708 designated resources and 1,823
  contributors to designated historic districts.  The layer carries no
  licence, only the City/SanGIS warranty disclaimer, so it is displayed with
  attribution only, not licensed for reuse.
  Each row's `feature_nm` is `HRB #: 680, District Contributor #: N/A` and its
  `feature_de` says which kind it is; **the layer publishes no property name
  or address** (the names live in the City's Register of Designated Historic
  Sites, published only as PDF and in the CHRID web database).  So:

  - a district contributor is a building inside a district, not a landmark of
    its own, and is not published (Columbus's rule for districts);
  - a designated resource publishes as `Historical Landmark No. 680`, the
    designation's number being all the layer says of it.  HRB number 0
    (a placeholder, not a designation; 88 points) is skipped.  The number is
    the key (`sd-hrb-680`); a number on more than one point (a resource of
    several buildings; 97 repeats) keeps its first point by OBJECTID.  1,522
    resources on 2026-11-18.

* **The National Register of Historic Places** (the National Park Service's
  `cultural_resources/nrhp_locations` MapServer, points then polygons) within
  `CITY_BOUNDS["USSAN"]` and with `City` one of San Diego, La Jolla or San
  Ysidro (two communities inside the City of San Diego that NPS writes as
  their own town) -- the box also holds Coronado, National City, Del Mar,
  Rancho Santa Fe and others.  Listed buildings, sites and structures, keyed
  `sd-nr-{NRIS_Refnum}` with `CertDate` as `year_designated`; districts are
  areas and are not published.  NPS data is a US federal work.

A building on both registers keeps both rows: the two designations are
separate facts, and `lm_type` says which register each row is from.
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
    iter_features,
)
from shared.ingest import CITY_BOUNDS, emit, make_point_wkt

HRB = FeatureLayer(
    "https://webmaps.sandiego.gov/arcgis/rest/services/Planning/"
    "Historic_Preservation_Resources/MapServer/0",
    timeout=180,
)
HRB_FIELDS = "OBJECTID,feature_nm,feature_de"

NRHP = "https://mapservices.nps.gov/arcgis/rest/services/cultural_resources/nrhp_locations/MapServer"
NRHP_LAYERS = (FeatureLayer(f"{NRHP}/0"), FeatureLayer(f"{NRHP}/1"))  # points first
NRHP_FIELDS = "OBJECTID,NRIS_Refnum,RESNAME,ResType,Address,City,CertDate,STATUS"
# NPS's `City` for listings inside the City of San Diego.
NRHP_CITIES = frozenset({"san diego", "la jolla", "san ysidro"})

LOCAL_TYPE = "City of San Diego Designated Historical Resource"
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


HRB_NUMBER = re.compile(r"HRB #:\s*(\d+)(?=[\s,]|$)")


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def fetch_all(layer: FeatureLayer, **kwargs) -> list[dict]:
    return [feature for page in iter_features(layer, **kwargs) for feature in page]


def hrb_number(feature_nm: str | None) -> str | None:
    """`HRB #: 680, District Contributor #: N/A` -> `680`."""
    text = clean(feature_nm) or ""
    if not text.startswith("HRB #:"):
        return None
    # A few rows drop the comma (`HRB #: 1521 District Contributor #: N/A`).
    match = HRB_NUMBER.match(text)
    if match:
        return match.group(1)
    return text[len("HRB #:"):].split(",", 1)[0].strip() or None


def city_register() -> dict[str, list]:
    rows: dict[str, list] = {k: [] for k in COLUMNS}
    features = fetch_all(HRB, out_fields=HRB_FIELDS, return_geometry=True, out_sr=4326)
    if not features:
        raise RuntimeError(f"{HRB.base} returned no resources")
    skipped: Counter[str] = Counter()
    seen: set[str] = set()
    for feature in sorted(features, key=lambda f: f["attributes"]["OBJECTID"]):
        attrs = feature.get("attributes") or {}
        kind = (clean(attrs.get("feature_de")) or "").lower()
        if kind.startswith("district contributor"):
            skipped["district contributor"] += 1
            continue
        if not kind.startswith("designated historical resource"):
            raise RuntimeError(
                f"San Diego HRB layer: unrecognised feature_de {attrs.get('feature_de')!r}"
            )
        number = hrb_number(attrs.get("feature_nm"))
        if number is None:
            raise RuntimeError(
                f"San Diego HRB layer: unreadable feature_nm {attrs.get('feature_nm')!r}"
            )
        if not number.isdigit() or int(number) == 0:
            skipped[f"HRB number {number!r}"] += 1
            continue
        geom = feature.get("geometry") or {}
        wkt = make_point_wkt(geom.get("x"), geom.get("y"))
        if wkt is None:
            skipped["no point"] += 1
            continue
        if number in seen:
            skipped["second point of one HRB number"] += 1
            continue
        seen.add(number)
        rows["landmark_id"].append(f"sd-hrb-{number}")
        rows["name"].append(f"Historical Landmark No. {number}")
        rows["geometry_raw"].append(wkt)
        rows["address"].append(None)
        rows["lm_type"].append(LOCAL_TYPE)
        rows["register_number"].append(f"HRB {number}")
        rows["year_designated"].append(None)
    print(
        f"San Diego HRB register: {len(rows['landmark_id'])} designated resources; "
        f"skipped {dict(skipped)}",
        file=sys.stderr,
    )
    return rows


def national_register() -> dict[str, list]:
    lat_min, lat_max, lon_min, lon_max = CITY_BOUNDS["USSAN"]
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
            if not ref or not name or wkt is None or ref in seen:
                continue
            if (clean(attrs.get("City")) or "").lower() not in NRHP_CITIES:
                continue
            if clean(attrs.get("STATUS")) != "Listed":
                continue
            if (clean(attrs.get("ResType")) or "").lower() == "district":
                continue
            seen.add(ref)
            cert = clean(attrs.get("CertDate")) or ""
            rows["landmark_id"].append(f"sd-nr-{ref}")
            rows["name"].append(name)
            rows["geometry_raw"].append(wkt)
            rows["address"].append(clean(attrs.get("Address")))
            rows["lm_type"].append(NATIONAL_TYPE)
            rows["register_number"].append(ref)
            rows["year_designated"].append(int(cert[:4]) if cert[:4].isdigit() else None)
    print(f"San Diego National Register: {len(rows['landmark_id'])} listings", file=sys.stderr)
    return rows


def build() -> pa.Table:
    local = city_register()
    national = national_register()
    data = {k: local[k] + national[k] for k in COLUMNS}
    n = len(data["landmark_id"])
    return pa.table(
        {
            "landmark_id": pa.array(data["landmark_id"], type=pa.string()),
            "name": pa.array(data["name"], type=pa.string()),
            "city": pa.array(["USSAN"] * n, type=pa.string()),
            "geometry_raw": pa.array(data["geometry_raw"], type=pa.string()),
            "address": pa.array(data["address"], type=pa.string()),
            "lm_type": pa.array(data["lm_type"], type=pa.string()),
            "register_number": pa.array(data["register_number"], type=pa.string()),
            "year_designated": pa.array(data["year_designated"], type=pa.int64()),
        }
    )


if __name__ == "__main__":
    emit(build())
