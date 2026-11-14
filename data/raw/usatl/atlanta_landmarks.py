#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Atlanta's designated landmark and historic buildings and sites.

Source: layer 16, "Landmark Building Site", of `OpenDataService1/FeatureServer`
on the City of Atlanta's own ArcGIS Server (gis.atlantaga.gov, Department of
City Planning): the parcels the city has designated under its historic
preservation ordinance as a Landmark Building/Site (`OVERLAY = 'LBS'`) or a
Historic Building/Site (`'HBS'`), each zoning overlay administered by the
Atlanta Urban Design Commission.  74 polygons when the city was wired
(2026-11-11), designated 1989-2026: the Fox Theatre, the Candler and Flatiron
buildings, the Wren's Nest, Swan House, City Hall.  The layer publishes no
licence; it is the city's open-data service.

An official register, live on a portal, is the runbook's first preference, so
there is no Nominatim geocoding, no committed CSV and nothing to stage.

The register's own landmark districts (Druid Hills, Inman Park, Grant Park and
sixteen more) are layer 15 of the same service; they are neighbourhoods
hundreds of acres across rather than places, and are left out.  Layer 17,
"Historic Building", is a 2005-06 survey of potentially eligible buildings,
not a designation, and is not used either.

* `landmark_id` is `GLOBALID` (74 distinct, none null; braces stripped).
* `name` is `BUILDING_N`; `address` is `STREET_ADD`, whitespace collapsed (the
  layer has a stray backtick on one).
* `lm_type` names the overlay, which is the legal designation.  `FIELD5`
  says "Landmark" or "Historic" in words but disagrees with the overlay on
  two rows (Hirsch Hall and the Imperial Hotel are `LBS` sites labelled
  "Historic"), so it is not used.  An overlay other than `LBS` or `HBS`
  raises.
* `property_type` is `BUILDING_T` (Commercial, Institutional, SF Residential,
  ...), published as written, typos included (`Commerial`).
* `designation_date` is `DESIGNATIO`.
* The geometry is the designated parcel, reprojected to WGS84 server-side.
  One row, the Fulton County African American Alms House (`OBJECTID` 4482,
  added in 2026 with no designation date), has an empty polygon and is skipped
  with a logged line, so 73 are published.
"""

import sys
from datetime import date
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import (
    FeatureLayer,
    esri_geometry_to_wkt,
    esri_ms_to_date,
    iter_features,
)
from shared.ingest import emit

LAYER = FeatureLayer(
    "https://gis.atlantaga.gov/dpcd/rest/services/OpenDataService1/FeatureServer/16"
)

OUT_FIELDS = "OBJECTID,GLOBALID,BUILDING_N,STREET_ADD,BUILDING_T,DESIGNATIO,OVERLAY"

OVERLAYS = {
    "LBS": "Landmark Building/Site",
    "HBS": "Historic Building/Site",
}


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).replace("`", "").split())
    return text or None


def lm_type_of(value) -> str:
    code = clean(value) or ""
    if code not in OVERLAYS:
        raise RuntimeError(
            f"Atlanta landmark OVERLAY {code!r} is new; decide what designation it is"
        )
    return OVERLAYS[code]


def transform(features: list[dict]) -> pa.Table:
    landmark_id: list[str] = []
    name: list[str] = []
    geometry_raw: list[str] = []
    address: list[str | None] = []
    property_type: list[str | None] = []
    lm_type: list[str] = []
    designation_date: list[date | None] = []

    for feature in features:
        attrs = feature.get("attributes") or {}
        raw_id = clean(attrs.get("GLOBALID"))
        label = clean(attrs.get("BUILDING_N"))
        wkt = esri_geometry_to_wkt(feature.get("geometry"))
        if not raw_id or not label or wkt is None:
            print(
                f"Atlanta landmarks: skipped OBJECTID {attrs.get('OBJECTID')} "
                "(no id, name or geometry)",
                file=sys.stderr,
            )
            continue
        landmark_id.append(f"atl-{raw_id.strip('{}').lower()}")
        name.append(label)
        geometry_raw.append(wkt)
        address.append(clean(attrs.get("STREET_ADD")))
        property_type.append(clean(attrs.get("BUILDING_T")))
        lm_type.append(lm_type_of(attrs.get("OVERLAY")))
        designation_date.append(esri_ms_to_date(attrs.get("DESIGNATIO")))

    return pa.table(
        {
            "landmark_id": pa.array(landmark_id, type=pa.string()),
            "name": pa.array(name, type=pa.string()),
            "city": pa.array(["USATL"] * len(landmark_id), type=pa.string()),
            "geometry_raw": pa.array(geometry_raw, type=pa.string()),
            "address": pa.array(address, type=pa.string()),
            "property_type": pa.array(property_type, type=pa.string()),
            "lm_type": pa.array(lm_type, type=pa.string()),
            "designation_date": pa.array(designation_date, type=pa.date32()),
        }
    )


if __name__ == "__main__":
    features: list[dict] = []
    for page in iter_features(
        LAYER, out_fields=OUT_FIELDS, return_geometry=True, out_sr=4326, order_by="OBJECTID"
    ):
        features.extend(page)
    emit(transform(features))
