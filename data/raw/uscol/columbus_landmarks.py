#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Columbus, OH's Register of Historic Properties and National Register listings.

Two registers, both read live from ArcGIS layers:

* **The Columbus Register of Historic Properties**, the city's own
  designation register (part of the zoning code; listed properties are subject
  to design review by the Historic Resources Commission), published by the
  Department of Development as `Schemas/Development/MapServer/0` on
  maps2.columbus.gov (ArcGIS item 0f7a15c4cea0418b8162de5a704aeb2b, licence
  CC0 per its `licenseInfo` and on opendata.columbus.gov).  66 points on
  2026-11-16, each with the property name, address, its roster number
  (`CR-40`, unique) and ordinance, and the date it was listed.  The roster
  number is the key (`col-cr-40`) and `DATE_LISTED` gives `year_designated`.
  One entry is marked demolished in its own name ("Felton School (Demolished
  in 1990)") and is not published: the building is gone.  The register's
  districts (layer 18, "Historic Districts") are areas, not landmarks, and are
  not read.
* **The National Register of Historic Places** (the National Park Service's
  `cultural_resources/nrhp_locations` MapServer, points then polygons) within
  `CITY_BOUNDS["USCOL"]` and with `City = Columbus` -- the box also holds
  Dublin, Worthington, Bexley, Westerville and Canal Winchester listings.
  Listed buildings, sites and structures, keyed `col-nr-{NRIS_Refnum}` with
  `CertDate` as `year_designated`; districts are areas and are not published
  (Durham's rule).  NPS data is a US federal work.

Many buildings are on both registers (the city layer's `NATIONAL_REGISTER`
says which); both rows are kept, as Naperville and Naples keep a building on
two registers, because the two designations are separate facts.  `lm_type`
says which register each row is from.
"""

import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import (
    FeatureLayer,
    esri_ms_to_date,
    iter_features,
)
from shared.ingest import emit, make_point_wkt
from shared.nrhp import national_register as shared_national_register  # noqa: E402

REGISTER = FeatureLayer(
    "https://maps2.columbus.gov/arcgis/rest/services/Schemas/Development/MapServer/0"
)
REGISTER_FIELDS = "OBJECTID,PROPERTY,ADDRESS,ROSTER_NUMBER,DATE_LISTED"


LOCAL_TYPE = "Columbus Register of Historic Properties"

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


def fetch_all(layer: FeatureLayer, **kwargs) -> list[dict]:
    return [feature for page in iter_features(layer, **kwargs) for feature in page]


def columbus_register() -> dict[str, list]:
    rows: dict[str, list] = {k: [] for k in COLUMNS}
    features = fetch_all(REGISTER, out_fields=REGISTER_FIELDS, return_geometry=True, out_sr=4326)
    if not features:
        raise RuntimeError(f"{REGISTER.base} returned no properties")
    seen: set[str] = set()
    for feature in sorted(features, key=lambda f: f["attributes"]["OBJECTID"]):
        attrs = feature.get("attributes") or {}
        geom = feature.get("geometry") or {}
        name = clean(attrs.get("PROPERTY"))
        roster = clean(attrs.get("ROSTER_NUMBER"))
        wkt = make_point_wkt(geom.get("x"), geom.get("y"))
        if not name or not roster or wkt is None:
            continue
        if "demolished" in name.lower():
            continue
        if roster in seen:
            raise RuntimeError(f"Columbus Register roster number {roster} is repeated")
        seen.add(roster)
        listed = esri_ms_to_date(attrs.get("DATE_LISTED"))
        rows["landmark_id"].append(f"col-{roster.lower()}")
        rows["name"].append(name)
        rows["geometry_raw"].append(wkt)
        rows["address"].append(clean(attrs.get("ADDRESS")))
        rows["lm_type"].append(LOCAL_TYPE)
        rows["register_number"].append(roster)
        rows["year_designated"].append(listed.year if listed else None)
    return rows


def national_register() -> dict[str, list]:
    return shared_national_register("USCOL", "Columbus", "col", COLUMNS, required=False)


def build() -> pa.Table:
    local = columbus_register()
    national = national_register()
    data = {k: local[k] + national[k] for k in COLUMNS}
    n = len(data["landmark_id"])
    return pa.table(
        {
            "landmark_id": pa.array(data["landmark_id"], type=pa.string()),
            "name": pa.array(data["name"], type=pa.string()),
            "city": pa.array(["USCOL"] * n, type=pa.string()),
            "geometry_raw": pa.array(data["geometry_raw"], type=pa.string()),
            "address": pa.array(data["address"], type=pa.string()),
            "lm_type": pa.array(data["lm_type"], type=pa.string()),
            "register_number": pa.array(data["register_number"], type=pa.string()),
            "year_designated": pa.array(data["year_designated"], type=pa.int64()),
        }
    )


if __name__ == "__main__":
    emit(build())
