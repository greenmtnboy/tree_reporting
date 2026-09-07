#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Burlington, Ontario's heritage properties.

Source: "Heritage Properties" (`COB/HeritageProperties/MapServer/0`) on the
same on-prem ArcGIS Server as the trees -- the city's municipal heritage
register, 294 properties, each with the year it was built, a short description
and whether it is designated under Ontario's Heritage Act or merely listed on
the register.

This is the runbook's first-preference landmark source: an official
designation registry, on the same portal as the trees, read live.

The city also publishes a "Heritage Register" table on ArcGIS Online
(`Heritage_Register_update`), which is the same register kept as a *table* --
296 rows with no geometry at all, addressed only by street number and name.
It is not used: a landmark with no coordinate would need Nominatim geocoding
to place, and this layer already carries the point.

**49 of the 294 have no `NAME`.**  As in Ottawa, an unnamed entry is a house
the register identifies by address, so `HOUSENUM STREET_NAME` is the fallback
label rather than dropping the row.
"""

import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from _arcgis_shared import FeatureLayer, esri_geometry_to_wkt, iter_features
from _ingest_shared import emit

LAYER = FeatureLayer(
    "https://mapping.burlington.ca/arcgisweb/rest/services/COB/"
    "HeritageProperties/MapServer/0",
    timeout=180,
)

OUT_FIELDS = (
    "OBJECTID,NAME,DESCRIPTION,YEARBUILT,TYPE,DESIGNATION,HOUSENUM,STREET_NAME"
)


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def parse_year(value) -> int | None:
    """The first four-digit year in a value, or None."""
    if value in (None, ""):
        return None
    digits = ""
    for ch in str(value):
        if ch.isdigit():
            digits += ch
            if len(digits) == 4:
                return int(digits)
        elif digits:
            digits = ""
    return None


def street_address(rec: dict) -> str | None:
    """`HOUSENUM STREET_NAME`, with the register's `0000` placeholder dropped."""
    number = clean(rec.get("HOUSENUM"))
    if number and set(number) == {"0"}:
        number = None
    street = clean(rec.get("STREET_NAME"))
    return " ".join(p for p in (number, street) if p) or None


def transform(features: list[dict]) -> pa.Table:
    landmark_id: list[str] = []
    city: list[str] = []
    name: list[str] = []
    geometry_raw: list[str | None] = []
    address: list[str | None] = []
    year_built: list[int | None] = []
    property_type: list[str | None] = []

    for feature in features:
        rec = feature.get("attributes") or {}
        raw_id = rec.get("OBJECTID")
        if raw_id in (None, ""):
            continue
        wkt = esri_geometry_to_wkt(feature.get("geometry"))
        if wkt is None:
            continue
        street = street_address(rec)
        label = clean(rec.get("NAME")) or street
        if label is None:
            continue

        # `OBJECTID` is the last resort of the runbook's three id choices and
        # is what this layer leaves: it publishes no GLOBALID, and `RSN` --
        # the field named like a record serial number -- is 0 on every row.
        landmark_id.append(f"bur-{raw_id}")
        city.append("CABUR")
        name.append(label)
        geometry_raw.append(wkt)
        address.append(street)
        year_built.append(parse_year(rec.get("YEARBUILT")))
        # `TYPE` is BLD or ROW -- a building, or a feature in the road
        # allowance such as a gateway or a monument.
        property_type.append(clean(rec.get("TYPE")))

    return pa.table(
        {
            "landmark_id": pa.array(landmark_id, type=pa.string()),
            "city": pa.array(city, type=pa.string()),
            "name": pa.array(name, type=pa.string()),
            "geometry_raw": pa.array(geometry_raw, type=pa.string()),
            "address": pa.array(address, type=pa.string()),
            "year_built": pa.array(year_built, type=pa.int64()),
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
