#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Ottawa's heritage cultural spaces.

Source: "Cultural Spaces Inventory - Heritage"
(`OfficialPlan/MapServer/133`) on open.ottawa.ca -- the heritage slice of the
city's cultural spaces inventory, 218 named places: museums, historic sites,
heritage places of worship, and the historic government buildings that are
most of downtown Ottawa.  Every row carries a name, an address, a `GLOBALID`
and a coordinate.  The 13 rows flagged `ACTIVE = 'No'` are dropped, leaving
205.

**The city's actual designation register was the first choice and was not
taken.**  `Planning/MapServer/61`, "Individually Designated Properties (Part
IV)", holds 444 properties designated under Ontario's Heritage Act and is
served publicly from the same map server.  Two things decided against it.  It
is not listed in open.ottawa.ca's DCAT catalogue -- "Heritage Conservation
Districts (Part V)" from the same service is, under Ottawa's Open Data Licence
2.0, but the Part IV layer is not -- so its licence is not stated anywhere,
which is the objection this repo already raised against ingesting CIF.  And
68 of its 444 rows have no name at all: a designated house is identified by
its address, so a third of that source would publish landmarks called
"10, prom Lady Grey Dr", which is worse context than the smaller named list.

The Heritage Conservation Districts layer is catalogued and is deliberately
not unioned in either, for the reason Halifax's districts are not: a district
is an area containing many of these places, so including both double-counts
them and drops a pin in the middle of a neighbourhood.
"""

import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from _arcgis_shared import FeatureLayer, esri_geometry_to_wkt, iter_features
from _ingest_shared import emit

LAYER = FeatureLayer(
    "https://maps.ottawa.ca/arcgis/rest/services/OfficialPlan/MapServer/133",
    timeout=180,
)

OUT_FIELDS = "GLOBALID,NAME,ADDRESS,SUB_CATEGORY,ACTIVE"

# 13 rows are recorded as no longer active -- a museum that has closed, a site
# that has been redeveloped.  A landmark nobody can visit is the same problem
# as a demolished heritage entry or a removed tree.
WHERE = "ACTIVE <> 'No'"


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
        raw_id = str(rec.get("GLOBALID") or "").strip()
        if not raw_id:
            continue
        label = clean(rec.get("NAME"))
        if label is None:
            continue
        wkt = esri_geometry_to_wkt(feature.get("geometry"))
        if wkt is None:
            continue

        landmark_id.append(f"ott-{raw_id}")
        city.append("CAOTT")
        name.append(label)
        geometry_raw.append(wkt)
        address.append(clean(rec.get("ADDRESS")))
        # A comma-separated list of tags rather than one value -- "Museum,
        # Historic Sites", "Place of Worship, Historic Sites" -- kept as
        # written, because which tag leads is the inventory's own judgement.
        property_type.append(clean(rec.get("SUB_CATEGORY")))

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
