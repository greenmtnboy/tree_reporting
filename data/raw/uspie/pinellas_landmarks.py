#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Pinellas County, Florida's historic landmarks and National Register listings.

Two registers, both read live from ArcGIS layers:

* **The County's Historic Landmarks** (unincorporated Pinellas), the hosted
  view `Pinellas_HistoricLandmarkUninc_view/FeatureServer/0` in the County's
  own organisation (ArcGIS item a050d3efccf2453091db507bb71be718, owner
  `PinellasCountyGIS`, data steward Pinellas County Housing & Community
  Development/Planning): the landmarks and landmark sites the Board of County
  Commissioners has designated under Chapter 146 of the Land Development
  Code.  No licence is stated (the item's `licenseInfo` is an "as is", "for
  reference only" disclaimer); displayed with attribution only.  Five parcel
  polygons on 2026-11-27.  Keyed by the layer's GlobalID
  (`pinellas-lhl-{guid}`): the layer has no designation number.  Two are
  named `PRIVATE RESIDENCE` and `PRIVATE BUSINESS` -- the County withholds
  the name -- and are not published, so three are.  `CITY` reads `PALM HARBOR` on every
  row, Fort De Soto included, so it is not read.
* **The National Register of Historic Places** (the National Park Service's
  `cultural_resources/nrhp_locations` MapServer, points then polygons) with
  `County = Pinellas` and `State = FLORIDA` -- the County's area, every
  municipality in it, rather than one `City`.  Listed buildings, sites,
  structures and objects, keyed `pinellas-nr-{NRIS_Refnum}` with `CertDate`
  as `year_designated`; districts are areas and are not published (Durham's
  rule).  NPS data is a US federal work.

A property on both registers keeps both rows, as Columbus and Baton Rouge
do: the two designations are separate facts.  `lm_type` says which register
each row is from.
"""

import re
import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_geometry_to_wkt, iter_features  # noqa: E402
from shared.ingest import emit  # noqa: E402
from shared.nrhp import NATIONAL_TYPE, NRHP_FIELDS, NRHP_LAYERS  # noqa: E402

LOCAL = FeatureLayer(
    "https://services.arcgis.com/f5HgUpxURgEzTccH/arcgis/rest/services/"
    "Pinellas_HistoricLandmarkUninc_view/FeatureServer/0"
)
LOCAL_FIELDS = "OBJECTID_1,GLOBALID,NAME,ADDRESS"
WITHHELD_NAMES = frozenset({"private residence", "private business"})

NRHP_WHERE = "County = 'Pinellas' AND State = 'FLORIDA'"

LOCAL_TYPE = "Pinellas County historic landmark"

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


def title_case(value: str | None) -> str | None:
    """The register's capitals as a name: `OZONA VILLAGE HALL` -> `Ozona Village Hall`."""
    if value is None:
        return None
    return " ".join(
        word.lower() if word[0].isdigit()  # 11TH -> 11th
        else re.sub(r"[A-Za-z]+", lambda m: m.group(0).capitalize(), word)  # (HARTLEY -> (Hartley
        for word in value.split()
    )


def fetch_all(layer: FeatureLayer, **kwargs) -> list[dict]:
    return [feature for page in iter_features(layer, **kwargs) for feature in page]


def local_register() -> dict[str, list]:
    rows: dict[str, list] = {k: [] for k in COLUMNS}
    features = fetch_all(
        LOCAL, out_fields=LOCAL_FIELDS, return_geometry=True, out_sr=4326, order_by="OBJECTID_1"
    )
    if not features:
        raise RuntimeError(f"{LOCAL.base} returned no landmarks")
    seen: set[str] = set()
    for feature in features:
        attrs = feature.get("attributes") or {}
        guid = str(attrs.get("GLOBALID") or "").strip("{} ").lower()
        if not guid:
            continue
        if guid in seen:
            raise RuntimeError(f"Pinellas County landmark GlobalID {guid} is repeated")
        seen.add(guid)
        name = clean(attrs.get("NAME"))
        wkt = esri_geometry_to_wkt(feature.get("geometry"))
        if not name or name.lower() in WITHHELD_NAMES or wkt is None:
            continue
        rows["landmark_id"].append(f"pinellas-lhl-{guid}")
        rows["name"].append(title_case(name))
        rows["geometry_raw"].append(wkt)
        rows["address"].append(title_case(clean(attrs.get("ADDRESS"))))
        rows["lm_type"].append(LOCAL_TYPE)
        rows["register_number"].append(None)
        rows["year_designated"].append(None)
    return rows


def national_register() -> dict[str, list]:
    seen: set[str] = set()
    rows: dict[str, list] = {k: [] for k in COLUMNS}
    for layer in NRHP_LAYERS:
        for feature in fetch_all(
            layer, out_fields=NRHP_FIELDS, return_geometry=True, out_sr=4326, where=NRHP_WHERE
        ):
            attrs = feature.get("attributes") or {}
            ref = clean(attrs.get("NRIS_Refnum"))
            name = clean(attrs.get("RESNAME"))
            wkt = esri_geometry_to_wkt(feature.get("geometry"))
            if not ref or not name or wkt is None or ref in seen:
                continue
            if clean(attrs.get("STATUS")) != "Listed":
                continue
            if (clean(attrs.get("ResType")) or "").lower() == "district":
                continue
            seen.add(ref)
            cert = clean(attrs.get("CertDate")) or ""
            rows["landmark_id"].append(f"pinellas-nr-{ref}")
            rows["name"].append(name)
            rows["geometry_raw"].append(wkt)
            rows["address"].append(clean(attrs.get("Address")))
            rows["lm_type"].append(NATIONAL_TYPE)
            rows["register_number"].append(ref)
            rows["year_designated"].append(int(cert[:4]) if cert[:4].isdigit() else None)
    if not rows["landmark_id"]:
        raise RuntimeError("no National Register listings found in Pinellas County")
    return rows


def build() -> pa.Table:
    local = local_register()
    national = national_register()
    data = {k: local[k] + national[k] for k in COLUMNS}
    n = len(data["landmark_id"])
    return pa.table(
        {
            "landmark_id": pa.array(data["landmark_id"], type=pa.string()),
            "name": pa.array(data["name"], type=pa.string()),
            "city": pa.array(["USPIE"] * n, type=pa.string()),
            "geometry_raw": pa.array(data["geometry_raw"], type=pa.string()),
            "address": pa.array(data["address"], type=pa.string()),
            "lm_type": pa.array(data["lm_type"], type=pa.string()),
            "register_number": pa.array(data["register_number"], type=pa.string()),
            "year_designated": pa.array(data["year_designated"], type=pa.int64()),
        }
    )


if __name__ == "__main__":
    emit(build())
