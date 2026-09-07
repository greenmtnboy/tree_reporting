#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Moncton's cultural assets, filtered to the ones that are places.

Source: "Cultural Assets" (`Cultural_Assets/FeatureServer/0`) on the same
ArcGIS Online organisation as the trees -- 646 named assets across six
categories, published bilingually, and including the city's designated
heritage properties (`SECSUBCAT = 'DESIGNATED HERITAGE PROPERTIES'`).

**This is not the obvious first choice and the obvious one is worse.**  Moncton
also publishes "Heritage Properties" (140 parcel polygons, designated under
By-law Z-1116), which is the runbook's first-preference kind of source -- an
official designation registry.  It carries no name: its only descriptive field
is `LOCATION`, a street address, so every landmark would be called "83 Church
st".  The cultural asset inventory carries `ASSETNAME` on all 646 rows and
contains the same designated properties under their actual names, so it is
read instead.

**Two of the six categories are filtered out**, because a landmark has to be a
place:

    Cultural Facilities        157   published -- museums, theatres, galleries
    Cultural Heritage          151   published -- designated and built heritage
    Community Cultural Orgs     19   published
    Natural Heritage             9   published
    Cultural Enterprises       260   dropped -- private businesses
    Festivals and Events        50   dropped -- an event is not a place

`Cultural Enterprises` is a business directory (shops, studios, restaurants)
rather than a set of landmarks, and a festival's row describes a recurring
event whose location is somebody else's venue -- publishing it would put a pin
labelled with a festival's name on a park that is already in the list.
"""

import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from _arcgis_shared import FeatureLayer, esri_geometry_to_wkt, iter_features
from _ingest_shared import emit

LAYER = FeatureLayer(
    "https://services1.arcgis.com/E26PuSoie2Y7bbyI/arcgis/rest/services/"
    "Cultural_Assets/FeatureServer/0",
    timeout=180,
)

OUT_FIELDS = "GlobalID,ASSETNAME,ADDRESS,CATEGORY,SUBCAT,SECSUBCAT"

PLACE_CATEGORIES = (
    "Cultural Facilities",
    "Cultural Heritage",
    "Community Cultural Orgs",
    "Natural Heritage",
)
WHERE = "CATEGORY IN (" + ", ".join(f"'{c}'" for c in PLACE_CATEGORIES) + ")"


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
    property_type: list[str | None] = []

    for feature in features:
        rec = feature.get("attributes") or {}
        raw_id = str(rec.get("GlobalID") or "").strip()
        if not raw_id:
            continue
        label = clean(rec.get("ASSETNAME"))
        if label is None:
            continue
        wkt = esri_geometry_to_wkt(feature.get("geometry"))
        if wkt is None:
            continue

        landmark_id.append(f"mon-{raw_id}")
        city.append("CAMON")
        name.append(label)
        geometry_raw.append(wkt)
        address.append(clean(rec.get("ADDRESS")))
        # `SUBCAT` is the useful grain -- "BUILT HERITAGE PROPERTIES",
        # "MUSEUMS", "PERFORMANCE SPACES" -- where CATEGORY is one of six.
        property_type.append(clean(rec.get("SUBCAT")))

    return pa.table(
        {
            "landmark_id": pa.array(landmark_id, type=pa.string()),
            "city": pa.array(city, type=pa.string()),
            "name": pa.array(name, type=pa.string()),
            "geometry_raw": pa.array(geometry_raw, type=pa.string()),
            "address": pa.array(address, type=pa.string()),
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
