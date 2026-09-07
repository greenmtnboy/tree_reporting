#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Victoria's prominent heritage sites.

Source: "Prominent Heritage Sites (OCP)"
(`OpenData_PlanningAndDevelopment/MapServer/8`) on `opendata.victoria.ca` --
the 23 buildings the Official Community Plan names as prominent heritage
landmarks: Craigdarroch Castle, the Belfry Theatre, the Empress, St Ann's
Academy.  Read live off the same portal as the trees, no geocoding and no
staging object.

**Twenty-three, and the 933-row layer next to it was passed over.**  Victoria
also publishes "Heritage Properties" (`MapServer/10`), forty times larger and
the obvious first choice -- until you read its schema, which is `OBJECTID`,
`Shape`, and a single `Heritage` column whose value is the word "Registered".
It carries **no name field at all**.  `name` is what the map's landmark layer
renders and what the agent uses to name the ground, so 933 unnamed parcels are
not a better answer than 23 named buildings; they are no answer.  If that
layer ever gains a name column it is the better source and this should move to
it.

The city's "Public Art, Monuments, Memorials, Plaques" layer (340 rows) is the
other candidate, and it is deliberately not unioned in: its `Title` is null on
most rows, and a plaque is a different kind of thing from a heritage building
-- mixing them would make "landmarks in Victoria" mean two things at once.
"""

import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from _arcgis_shared import FeatureLayer, esri_geometry_to_wkt, iter_features
from _ingest_shared import emit

LAYER = FeatureLayer(
    "https://maps.victoria.ca/server/rest/services/OpenData/"
    "OpenData_PlanningAndDevelopment/MapServer/8"
)

OUT_FIELDS = "OBJECTID,Name,Address,Protected"


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
        label = clean(rec.get("Name"))
        if label is None:
            continue
        wkt = esri_geometry_to_wkt(feature.get("geometry"))
        if wkt is None:
            continue
        oid = rec.get("OBJECTID")
        if oid is None:
            continue

        # The layer publishes no id of its own -- no GlobalID, no OCP
        # reference number -- so the row number is what there is.  That is
        # tolerable for a landmark in a way it would not be for a tree_id:
        # nothing outside this table joins to it, and no check-in is recorded
        # against one.  Toronto's Places of Interest made the same call.
        landmark_id.append(f"vic-{oid}")
        city.append("CAVIC")
        name.append(label)
        geometry_raw.append(wkt)
        address.append(clean(rec.get("Address")))
        # "Yes"/"No" -- whether the site carries statutory protection as well
        # as OCP recognition.
        property_type.append(clean(rec.get("Protected")))

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
        LAYER, out_fields=OUT_FIELDS, return_geometry=True, out_sr=4326
    ):
        features.extend(page)
    emit(transform(features))
