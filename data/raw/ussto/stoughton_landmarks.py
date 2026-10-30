#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Stoughton, WI's designated historic landmarks.

Source: `Historical_Landmarks_WFL1/FeatureServer/0` on the city's ArcGIS
Online organisation, the same one that hosts the tree inventory: the
Stoughton Landmarks Commission's register, 29 points when the city was wired,
each with a name, address, year built, building type and the district it sits
in.  `Designations` reads `SLC` (local landmark) and, for about half, `NR`
(also on the National Register); every row is a designated landmark, so the
column decides nothing here.

An official register on the trees' own portal is the runbook's first
preference, so there is no Nominatim geocoding, no committed CSV and nothing
to stage.  The layer has no stable landmark number, so `OBJECTID` is the key.
"""

import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, iter_features
from shared.ingest import emit, make_point_wkt

LAYER = FeatureLayer(
    "https://services7.arcgis.com/iZIPdzAfqdnP9vrA/arcgis/rest/services/"
    "Historical_Landmarks_WFL1/FeatureServer/0"
)

OUT_FIELDS = "OBJECTID,Landmark_Name,Address,Year,Building_Type,Historic_District"


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def transform(features: list[dict]) -> pa.Table:
    landmark_id: list[str] = []
    name: list[str] = []
    geometry_raw: list[str] = []
    address: list[str | None] = []
    property_type: list[str | None] = []
    neighborhood: list[str | None] = []
    year_built: list[int | None] = []

    for feature in features:
        attrs = feature.get("attributes") or {}
        geom = feature.get("geometry") or {}
        label = clean(attrs.get("Landmark_Name"))
        wkt = make_point_wkt(geom.get("x"), geom.get("y"))
        if attrs.get("OBJECTID") is None or not label or wkt is None:
            continue
        landmark_id.append(f"sto-{attrs['OBJECTID']}")
        name.append(label)
        geometry_raw.append(wkt)
        address.append(clean(attrs.get("Address")))
        property_type.append(clean(attrs.get("Building_Type")))
        neighborhood.append(clean(attrs.get("Historic_District")))
        year = attrs.get("Year")
        year_built.append(int(year) if year else None)

    return pa.table(
        {
            "landmark_id": pa.array(landmark_id, type=pa.string()),
            "name": pa.array(name, type=pa.string()),
            "city": pa.array(["USSTO"] * len(landmark_id), type=pa.string()),
            "geometry_raw": pa.array(geometry_raw, type=pa.string()),
            "address": pa.array(address, type=pa.string()),
            "property_type": pa.array(property_type, type=pa.string()),
            "neighborhood": pa.array(neighborhood, type=pa.string()),
            "year_built": pa.array(year_built, type=pa.int64()),
        }
    )


if __name__ == "__main__":
    features: list[dict] = []
    for page in iter_features(
        LAYER, out_fields=OUT_FIELDS, return_geometry=True, order_by="OBJECTID"
    ):
        features.extend(page)
    emit(transform(features))
