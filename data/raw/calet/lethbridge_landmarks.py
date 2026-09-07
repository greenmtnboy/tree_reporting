#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Lethbridge's designated historic places.

Source: "Historic Places" (`OpenData/odl_historicplaces`) on
`opendata.lethbridge.ca` -- the 46 properties designated as Provincial or
Municipal Historic Resources under Alberta's Historical Resources Act, each
with its designating by-law, the date of designation and a link to its entry
in the Alberta Register of Historic Places.

This is the runbook's first-preference landmark source: an official
designation registry, on the same portal as the trees, read live.

**Forty-six is the whole register, not a sample.**  Lethbridge also publishes
`odl_monument` (345 rows), which looks larger and is not the same thing: it is
a parks *asset* layer of plaques, benches and playground signs -- "Agnes
Davidson Playground", "Fleetwood Bawden Play Area" -- with a maintenance
schedule and a replacement year attached.  Those are not landmarks; naming the
ground with them would bury the Galt Museum under school playgrounds.
"""

import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from _arcgis_shared import (
    FeatureLayer,
    esri_geometry_to_wkt,
    esri_ms_to_datetime,
    iter_features,
)
from _ingest_shared import emit

LAYER = FeatureLayer(
    "https://gis.lethbridge.ca/gisopendata/rest/services/OpenData/"
    "odl_historicplaces/MapServer/0"
)

OUT_FIELDS = "GlobalID,Name,DesignationType,Location,DesignationBylaw,DesignationDate"


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def transform(features: list[dict]) -> pa.Table:
    landmark_id: list[str] = []
    city: list[str] = []
    name: list[str] = []
    geometry_raw: list[str | None] = []
    address: list[str | None] = []
    year_designated: list[int | None] = []
    property_type: list[str | None] = []

    for feature in features:
        rec = feature.get("attributes") or {}
        label = clean(rec.get("Name"))
        if label is None:
            continue
        wkt = esri_geometry_to_wkt(feature.get("geometry"))
        if wkt is None:
            continue
        raw_id = str(rec.get("GlobalID") or "").strip().strip("{}")
        if not raw_id:
            continue

        landmark_id.append(f"let-{raw_id}")
        city.append("CALET")
        name.append(label)
        geometry_raw.append(wkt)
        address.append(clean(rec.get("Location")))
        designated = esri_ms_to_datetime(rec.get("DesignationDate"))
        year_designated.append(designated.year if designated else None)
        # "Provincial" or "Municipal" -- the level the resource is designated
        # at, which is the only classification the register carries.
        property_type.append(clean(rec.get("DesignationType")))

    return pa.table(
        {
            "landmark_id": pa.array(landmark_id, type=pa.string()),
            "city": pa.array(city, type=pa.string()),
            "name": pa.array(name, type=pa.string()),
            "geometry_raw": pa.array(geometry_raw, type=pa.string()),
            "address": pa.array(address, type=pa.string()),
            "year_designated": pa.array(year_designated, type=pa.int64()),
            "property_type": pa.array(property_type, type=pa.string()),
        }
    )


if __name__ == "__main__":
    features: list[dict] = []
    for page in iter_features(
        LAYER, out_fields=OUT_FIELDS, return_geometry=True, out_sr=4326
    ):
        features.extend(page)
    emit(transform(features))
