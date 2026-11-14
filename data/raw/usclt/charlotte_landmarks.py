#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Charlotte's designated historic landmarks.

Source: "Historic Properties Local" (`LocalHistoricProperty/FeatureServer/0`
on Mecklenburg County's ArcGIS Server, listed on data.charlottenc.gov): the
Charlotte-Mecklenburg Historic Landmarks Commission's register of locally
designated historic landmarks, one parcel polygon per property, with its
name, address, year built, property type, area, designation date and a link
to the Commission's page.  377 properties when the city was wired
(2026-11-11).  An official register, live on an ArcGIS portal, is the
runbook's first preference, so there is no geocoding, no committed CSV and
nothing to stage.

**The register is county-wide and the city is not.**  The Commission
designates across Mecklenburg County, so Davidson, Cornelius and north
Huntersville are on it; 38 properties lie outside `CITY_BOUNDS['USCLT']` and
are left out, filtered by the city's own bounds (the box its trees are
validated against and its OSM extract asks for) rather than a second copy of
them.  The layer's own `latitude`/`longitude` columns decide it.

`objectid` is the key: `pid` (the parcel number) repeats on five parcels
that hold more than one landmark.  `designation_status` is null on every row,
so it decides nothing; every row is a designated landmark.  `type` is the
property type, `location` the neighbourhood or town the Commission files it
under, `year` the year built and `desigdate` the designation date.  The
geometry is the parcel polygon, as WKT.
"""

import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import (
    FeatureLayer,
    esri_geometry_to_wkt,
    esri_ms_to_date,
    iter_features,
)
from shared.ingest import CITY_BOUNDS, emit

LAYER = FeatureLayer(
    "https://meckgis.mecklenburgcountync.gov/server/rest/services/"
    "LocalHistoricProperty/FeatureServer/0"
)

OUT_FIELDS = "objectid,name,address,type,location,year,desigdate,latitude,longitude"

LAT_MIN, LAT_MAX, LON_MIN, LON_MAX = CITY_BOUNDS["USCLT"]


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def in_city(attrs: dict) -> bool:
    lat, lon = attrs.get("latitude"), attrs.get("longitude")
    if lat is None or lon is None:
        return False
    return LAT_MIN <= lat <= LAT_MAX and LON_MIN <= lon <= LON_MAX


def transform(features: list[dict]) -> pa.Table:
    landmark_id: list[str] = []
    name: list[str] = []
    geometry_raw: list[str] = []
    address: list[str | None] = []
    property_type: list[str | None] = []
    neighborhood: list[str | None] = []
    year_built: list[int | None] = []
    designation_date: list = []

    outside = unusable = 0
    for feature in features:
        attrs = feature.get("attributes") or {}
        if not in_city(attrs):
            outside += 1
            continue
        label = clean(attrs.get("name"))
        wkt = esri_geometry_to_wkt(feature.get("geometry"))
        if attrs.get("objectid") is None or not label or wkt is None:
            unusable += 1
            continue
        landmark_id.append(f"clt-{attrs['objectid']}")
        name.append(label)
        geometry_raw.append(wkt)
        address.append(clean(attrs.get("address")))
        property_type.append(clean(attrs.get("type")))
        neighborhood.append(clean(attrs.get("location")))
        year = attrs.get("year")
        year_built.append(int(year) if year else None)
        designation_date.append(esri_ms_to_date(attrs.get("desigdate")))

    print(
        f"Charlotte landmarks: {len(landmark_id)} kept, {outside} outside the "
        f"city's bounds, {unusable} with no id, name or geometry",
        file=sys.stderr,
    )
    return pa.table(
        {
            "landmark_id": pa.array(landmark_id, type=pa.string()),
            "name": pa.array(name, type=pa.string()),
            "city": pa.array(["USCLT"] * len(landmark_id), type=pa.string()),
            "geometry_raw": pa.array(geometry_raw, type=pa.string()),
            "address": pa.array(address, type=pa.string()),
            "property_type": pa.array(property_type, type=pa.string()),
            "neighborhood": pa.array(neighborhood, type=pa.string()),
            "year_built": pa.array(year_built, type=pa.int64()),
            "designation_date": pa.array(designation_date, type=pa.date32()),
        }
    )


if __name__ == "__main__":
    features: list[dict] = []
    for page in iter_features(
        LAYER,
        out_fields=OUT_FIELDS,
        return_geometry=True,
        out_sr=4326,
        order_by="objectid",
    ):
        features.extend(page)
    emit(transform(features))
