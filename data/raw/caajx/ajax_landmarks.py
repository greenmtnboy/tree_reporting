#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Ajax's heritage inventory.

Source: "Heritage Inventory" (`Ajax_Open_Data/MapServer/2`) on the same
on-prem ArcGIS Server as the trees -- the town's heritage register, 257
entries: 31 designated under Ontario's Heritage Act, 149 listed on the
inventory, and 77 recorded as demolished.

This is the runbook's first-preference landmark source: an official
designation registry on the same portal as the trees, read live.

**The demolished entries are dropped.**  The register keeps them as a record
of what stood there; they are not landmarks anyone can visit, and a pin on a
building that no longer exists is the landmark equivalent of publishing a
removed tree.  180 entries are published.

**44 of the 257 have no `NAME`**, which is the usual pattern for a heritage
inventory of houses.  `LOCATION` is populated on every row and reads as an
address ("Rotherglen Road & Lincoln Avenue"), so it is the fallback label.
"""

import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from _arcgis_shared import FeatureLayer, esri_geometry_to_wkt, iter_features
from _ingest_shared import emit

LAYER = FeatureLayer(
    "https://ajaxmaps.ajax.ca/gisernie/rest/services/Public/"
    "Ajax_Open_Data/MapServer/2",
    timeout=180,
)

OUT_FIELDS = "OBJECTID,NAME,LOCATION,ADDRESS,YEAR_BUILT,TYPE,STATUS,DEMOLISHED"

WHERE = "DEMOLISHED IS NULL OR DEMOLISHED <> 'Yes'"


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
        street = clean(rec.get("LOCATION")) or clean(rec.get("ADDRESS"))
        label = clean(rec.get("NAME")) or street
        if label is None:
            continue

        # No GLOBALID on this layer, and `FACILITYID` is null on most rows --
        # `OBJECTID` is the runbook's last resort and what is left.
        landmark_id.append(f"ajx-{raw_id}")
        city.append("CAAJX")
        name.append(label)
        geometry_raw.append(wkt)
        address.append(street)
        year = rec.get("YEAR_BUILT")
        year_built.append(int(year) if isinstance(year, (int, float)) and year else None)
        # `STATUS` distinguishes a designated property from one merely listed.
        property_type.append(clean(rec.get("STATUS")))

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
        LAYER,
        out_fields=OUT_FIELDS,
        where=WHERE,
        return_geometry=True,
        out_sr=4326,
    ):
        features.extend(page)
    emit(transform(features))
