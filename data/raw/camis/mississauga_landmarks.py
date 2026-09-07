#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Mississauga's city landmarks, filtered to the ones that are places.

Source: "City Landmarks" (`CITY_POI/FeatureServer/0`) on data.mississauga.ca
-- the city's own points-of-interest register, 2,879 named features, edited
daily.

**The runbook's first-preference source exists here and cannot be used.**
Mississauga publishes "Mississauga Heritage Properties", 1,849 designated and
listed heritage parcels -- and the layer's only descriptive columns are
`HERC_DESCRIPTION` ("LISTED ON THE HERITAGE REGISTER BUT NOT DESIGNATED") and
`HERC_STATUS` ("L").  There is no name on any row, so every landmark would be
called the same thing.  A second layer, "City Heritage Buildings", is named but
holds seven rows.  The POI register is what carries both a name and a place.

**It is a register of city assets, so most of it is not a landmark.**  Of the
2,879 rows, 255 are playgrounds, 187 parking lots, 82 basketball nets, 66 shade
structures and so on down to a solar bench.  `LANDMARK_TYPES` below is the
filter: parks, heritage properties, museums, galleries, theatres, libraries,
arenas, community and senior centres, cemeteries, beaches, golf courses,
hospitals, colleges, cenotaphs, monuments, public art, government buildings and
the transit landmarks -- about 660 rows.  Everything excluded is an amenity
*inside* one of those places rather than a place someone would name.

The filter is written against `TYPEDESC`, the human-readable label, rather than
the `LANDMARKTYPE` code beside it (`PKBNDY`, `SBLDEP`, `TNSCLPL`): a reviewer
can check a list of English type names and cannot check a list of codes.
"""

import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from _arcgis_shared import FeatureLayer, esri_geometry_to_wkt, iter_features
from _ingest_shared import emit

LAYER = FeatureLayer(
    "https://services6.arcgis.com/hM5ymMLbxIyWTjn2/arcgis/rest/services/"
    "CITY_POI/FeatureServer/0",
    timeout=180,
)

OUT_FIELDS = "OBJECTID,LANDMARKNAME,TYPEDESC,STNO,STNAME,SUFFIX"

# The POI types that name a place.  Excluded: every court, pitch, diamond,
# rink, pad, playground, washroom, parking lot, shade structure, picnic table
# and equipment locker, and the schools -- an amenity inside a park is not a
# landmark, and 101 public elementary schools would swamp the rest.
LANDMARK_TYPES = (
    "Community Park",
    "Destination Park",
    "Greenlands Park",
    "Non Accessible Park",
    "Heritage Properties",
    "Museums",
    "Art Galleries",
    "Performing Arts, Theatres",
    "Theatre",
    "Libraries",
    "Arenas",
    "Community Centres, Major",
    "Senior Centres",
    "Cemeteries",
    "Beaches",
    "Golf Courses",
    "Hospitals",
    "Colleges, Universities",
    "Cenotaph",
    "Plaques And Monuments",
    "Cultural Feature",
    "Public Art",
    "Go Stations",
    "Transit Terminals",
    "Airport",
    "Bridge",
    "Government, City Of Mississauga",
    "Government, Peel Region",
    "Government Provincial",
    "Government Federal",
)
WHERE = "TYPEDESC IN (" + ", ".join(f"'{t}'" for t in LANDMARK_TYPES) + ")"


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
        raw_id = rec.get("OBJECTID")
        if raw_id in (None, ""):
            continue
        label = clean(rec.get("LANDMARKNAME"))
        if label is None:
            continue
        wkt = esri_geometry_to_wkt(feature.get("geometry"))
        if wkt is None:
            continue

        # The layer publishes no GLOBALID, so this is the runbook's last
        # resort: `OBJECTID` is a local row number a republish can reassign.
        landmark_id.append(f"mis-{raw_id}")
        city.append("CAMIS")
        name.append(label)
        geometry_raw.append(wkt)
        address.append(
            clean(
                " ".join(
                    p
                    for p in (
                        clean(rec.get("STNO")),
                        clean(rec.get("STNAME")),
                        clean(rec.get("SUFFIX")),
                    )
                    if p
                )
            )
        )
        property_type.append(clean(rec.get("TYPEDESC")))

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
