#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Lansing, Michigan's local historic designations and National Register listings.

Two registers, both read live from ArcGIS layers:

* **Lansing's locally designated historic properties**, read from the City's
  "Historic Properties" layer (ArcGIS item a0f8c6e4d0a44291a7711103367329f4,
  owner `Andrew.Fedewa_Lansing`, "Historic Properties within the city of
  Lansing", in the City of Lansing's ArcGIS Online organisation; the layer
  behind the City's "Lansing Historic Properties" StoryMap).  No licence
  stated; displayed with attribution only, not licensed for reuse.  182 parcel
  polygons on 2026-11-22, flagged `LOCALLIST`, `STATELIST` and `NRLIST`; only
  the 36 with `LOCALLIST = YES` (a local designation by the City) are read,
  less the 22 contributing parcels of the Cherry Hill local historic district
  (`DISTRICT` set; a district is an area, not a landmark), leaving 14 rows for
  10 properties (Prudden Wheel spans four parcels, Emery Houses two) --
  the 83 `MEMORANDUM 2012` rows are a survey's candidates, not designations,
  and the State and National Register rows come from their registers.  Keyed
  `lansing-lr-{GlobalID}`; the layer publishes no local designation date.
  Names are stored as UTF-8 read as Windows-1252 (`Arbaughâ€™s`) and are
  repaired.
* **The National Register of Historic Places** (the National Park Service's
  `cultural_resources/nrhp_locations` MapServer, points then polygons) within
  `CITY_BOUNDS["USLAN"]` and with `City = Lansing` -- the box also holds East
  Lansing's listings.  Listed buildings, sites and structures, keyed
  `lansing-nr-{NRIS_Refnum}` with `CertDate` as `year_designated`; districts
  are areas and are not published (Durham's rule).  NPS data is a US federal
  work.

A property on both registers keeps both rows, as Columbus does, because the
two designations are separate facts.  `lm_type` says which register each row
is from.
"""

import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_geometry_to_wkt, iter_features
from shared.ingest import CITY_BOUNDS, emit

REGISTER = FeatureLayer(
    "https://services1.arcgis.com/pNPbgWy7hpfFGWoZ/arcgis/rest/services/Historic_Properties/FeatureServer/0"
)
REGISTER_FIELDS = "OBJECTID,GlobalID,STNUM,STDIR,STNAME,RESOURCENAME,LOCALLIST,DISTRICT"

NRHP = "https://mapservices.nps.gov/arcgis/rest/services/cultural_resources/nrhp_locations/MapServer"
NRHP_LAYERS = (FeatureLayer(f"{NRHP}/0"), FeatureLayer(f"{NRHP}/1"))  # points first
NRHP_FIELDS = "OBJECTID,NRIS_Refnum,RESNAME,ResType,Address,City,CertDate,STATUS"

LOCAL_TYPE = "Lansing local historic designation"
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


def repair_mojibake(text: str | None) -> str | None:
    """Undo UTF-8 read as Windows-1252 (`Arbaughâ€™s` -> `Arbaugh's`)."""
    if text is None or "â€" not in text:
        return text
    try:
        return text.encode("cp1252").decode("utf-8")
    except UnicodeError:
        return text


def fetch_all(layer: FeatureLayer, **kwargs) -> list[dict]:
    return [feature for page in iter_features(layer, **kwargs) for feature in page]


def local_register() -> dict[str, list]:
    rows: dict[str, list] = {k: [] for k in COLUMNS}
    features = fetch_all(
        REGISTER, out_fields=REGISTER_FIELDS, return_geometry=True, out_sr=4326
    )
    if not features:
        raise RuntimeError(f"{REGISTER.base} returned no properties")
    seen: set[str] = set()
    for feature in features:
        attrs = feature.get("attributes") or {}
        if clean(attrs.get("LOCALLIST")) != "YES":
            continue
        if clean(attrs.get("DISTRICT")):
            # A contributing parcel of a local historic district (all 22 are
            # Cherry Hill's, each named after the district): an area, not a
            # landmark (Durham's rule).
            continue
        guid = (clean(attrs.get("GlobalID")) or "").strip("{}").lower()
        if not guid:
            raise RuntimeError(f"Lansing Historic Properties OBJECTID {attrs.get('OBJECTID')} has no GlobalID")
        if guid in seen:
            raise RuntimeError(f"Lansing Historic Properties GlobalID {guid} is repeated")
        seen.add(guid)
        name = repair_mojibake(clean(attrs.get("RESOURCENAME")))
        wkt = esri_geometry_to_wkt(feature.get("geometry"))
        if not name or wkt is None:
            continue
        number = clean(attrs.get("STNUM"))
        address = clean(
            " ".join(
                str(v or "")
                for v in (None if number == "0" else number, attrs.get("STDIR"), attrs.get("STNAME"))
            )
        )
        rows["landmark_id"].append(f"lansing-lr-{guid}")
        rows["name"].append(name)
        rows["geometry_raw"].append(wkt)
        rows["address"].append(address)
        rows["lm_type"].append(LOCAL_TYPE)
        rows["register_number"].append(None)
        rows["year_designated"].append(None)
    return rows


def national_register() -> dict[str, list]:
    lat_min, lat_max, lon_min, lon_max = CITY_BOUNDS["USLAN"]
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
            if (clean(attrs.get("City")) or "").lower() != "lansing":
                continue
            if clean(attrs.get("STATUS")) != "Listed":
                continue
            if (clean(attrs.get("ResType")) or "").lower() == "district":
                continue
            seen.add(ref)
            cert = clean(attrs.get("CertDate")) or ""
            rows["landmark_id"].append(f"lansing-nr-{ref}")
            rows["name"].append(name)
            rows["geometry_raw"].append(wkt)
            rows["address"].append(clean(attrs.get("Address")))
            rows["lm_type"].append(NATIONAL_TYPE)
            rows["register_number"].append(ref)
            rows["year_designated"].append(int(cert[:4]) if cert[:4].isdigit() else None)
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
            "city": pa.array(["USLAN"] * n, type=pa.string()),
            "geometry_raw": pa.array(data["geometry_raw"], type=pa.string()),
            "address": pa.array(data["address"], type=pa.string()),
            "lm_type": pa.array(data["lm_type"], type=pa.string()),
            "register_number": pa.array(data["register_number"], type=pa.string()),
            "year_designated": pa.array(data["year_designated"], type=pa.int64()),
        }
    )


if __name__ == "__main__":
    emit(build())
