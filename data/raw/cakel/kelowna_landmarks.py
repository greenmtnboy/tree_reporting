#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Kelowna's heritage registry.

Source: "Heritage Registry" (`OpenData_Planning_and_other/MapServer/9`) on
`opendata.kelowna.ca` -- the buildings on the city's heritage register, 215
parcels each carrying the building's name.  Read live off the same portal as
the trees, no geocoding and no staging object.

This is the runbook's first-preference landmark source.  Kelowna publishes two
neighbours to it -- "Heritage Designation Property" (`MapServer/8`, the
subset with a statutory designation by-law) and "Heritage Revitalization"
(`MapServer/10`, properties under a revitalisation agreement).  Both are
subsets of the register by another name, so unioning them would list the same
buildings two and three times.

**The register's key is the parcel *and* the building on it, so the id is
built from both.**  `KID` is the parcel, and a heritage site with several
registered buildings publishes one row each on one parcel: the Pandosy Mission
is eight rows -- chapel, barn, root house, blacksmith shop and four dwellings
-- sharing a single `KID`, a single `OBJECTID` and a single outline.  Seven
parcels are like that, 215 rows over 198 parcels.

Neither published column keys them.  `OBJECTID` is not the OID here (the layer
resource reports `objectIdField: null` -- it is a query layer, not a
registered feature class) and repeats exactly where `KID` does.  `landmark_id`
is the declared grain of this table and a repeat fans out every join built on
it, so the id carries the building name alongside the parcel.  Slugified, so
it survives a CSV and a URL, and stable: adding a ninth building to the
Pandosy Mission does not renumber the other eight, which is what a positional
suffix would have done.

The buildings are kept rather than merged.  Each is separately registered and
separately named, and merging them would throw away seven of the eight names
for no gain -- they would still render at one point, because they share the
outline.

One row *is* a true duplicate and is collapsed: Kelowna Memorial Park Cemetery
is published twice on the same parcel with the same name and the same outline,
which is one landmark listed twice rather than two things on one site.  Same
parcel and same name is the test, which is exactly the id -- so keeping the
first row per id both fixes the grain and states what a duplicate is.
"""

import re
import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from _arcgis_shared import FeatureLayer, esri_geometry_to_wkt, iter_features
from _ingest_shared import emit

LAYER = FeatureLayer(
    "https://geoportal.kelowna.ca/arcgis/rest/services/ArcGISOnline/"
    "OpenData_Planning_and_other/MapServer/9"
)

OUT_FIELDS = "KID,BLDG_NAME"


_NON_SLUG = re.compile(r"[^a-z0-9]+")


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def slug(value: str) -> str:
    """A building name reduced to id-safe characters."""
    return _NON_SLUG.sub("-", value.lower()).strip("-")


def transform(features: list[dict]) -> pa.Table:
    landmark_id: list[str] = []
    city: list[str] = []
    name: list[str] = []
    geometry_raw: list[str | None] = []
    seen: set[str] = set()

    for feature in features:
        rec = feature.get("attributes") or {}
        label = clean(rec.get("BLDG_NAME"))
        if label is None:
            continue
        wkt = esri_geometry_to_wkt(feature.get("geometry"))
        if wkt is None:
            continue
        kid = rec.get("KID")
        if kid is None:
            continue

        key = f"kel-{kid}-{slug(label)}"
        if key in seen:
            continue
        seen.add(key)

        landmark_id.append(key)
        city.append("CAKEL")
        name.append(label)
        geometry_raw.append(wkt)

    return pa.table(
        {
            "landmark_id": pa.array(landmark_id, type=pa.string()),
            "city": pa.array(city, type=pa.string()),
            "name": pa.array(name, type=pa.string()),
            "geometry_raw": pa.array(geometry_raw, type=pa.string()),
        }
    )


if __name__ == "__main__":
    features: list[dict] = []
    # Ordered by the register's own key rather than the default OBJECTID:
    # this layer reports `objectIdField: null` and its OBJECTID repeats, so it
    # is not a total order, and offset paging over one can repeat or skip rows.
    # Moot at 215 features against a 2,000-row page, and not moot the day the
    # register grows -- which is exactly the kind of silent truncation
    # `iter_features` refuses to allow by requiring an order at all.
    for page in iter_features(
        LAYER,
        out_fields=OUT_FIELDS,
        return_geometry=True,
        out_sr=4326,
        order_by="KID,BLDG_NAME",
    ):
        features.extend(page)
    emit(transform(features))
