#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Welland's (Ontario) designated heritage properties.

Source: "Welland Designated Heritage Properties" (ArcGIS item
0aa78d20dd6a4dac95e2e8e5bca0e8c2, owner `WellandGIS`: "polygons representing
the designated heritage properties in the City of Welland.  The information
was obtained from the Building Division and confirmed by the Clerks
department"), served from the City's own ArcGIS Server as
`OpenData/OPENDATA_Heritage_Parcels/MapServer/0`: 31 properties designated
by by-law under the Ontario Heritage Act on 2026-11-22, each the parcel
polygon from the City's cadastre.

LICENCE: Open Government Licence - City of Welland -- the item's
`licenseInfo` links https://open-welland.hub.arcgis.com/pages/terms-of-use,
the same licence as the tree layer (attribution "Contains information
licensed under the Open Government Licence – City of Welland.").  The layer
is in the City's OpenData service folder, though the Hub's catalogue feed does
not list it.

**The id is the designating by-law number (`wld-hp-{Bylaw_No}`)**, unique
over the register and the City's own key for a designation.  `Parcel_No` is
not: the Carnegie Building and the Welland Historical Museum are two
designations (by-laws 2006-101 and 2018-121) on one parcel, and both are
kept -- two landmarks with one polygon.  A repeated or missing by-law number
raises.

`CommonName` is filled on every row and is the name; `Address`, `Year_built`
and `Year_Desig` map to `address`, `year_built` and `year_designated`;
`property_type` is the designation.
"""

import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_geometry_to_wkt, iter_features
from shared.ingest import emit

LAYER = FeatureLayer(
    "https://arcgisweb.welland.ca/serverprodpub/rest/services/OpenData/"
    "OPENDATA_Heritage_Parcels/MapServer/0",
    timeout=180,
)
OUT_FIELDS = "OBJECTID,CommonName,Address,Bylaw_No,Year_built,Year_Desig"
PROPERTY_TYPE = "Designated (Ontario Heritage Act)"


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def year(value) -> int | None:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if 1500 <= n <= 2100 else None


def transform(features: list[dict]) -> pa.Table:
    landmark_id: list[str] = []
    name: list[str] = []
    geometry_raw: list[str] = []
    address: list[str | None] = []
    year_built: list[int | None] = []
    year_designated: list[int | None] = []
    seen: set[str] = set()
    skipped = 0

    for feature in features:
        rec = feature.get("attributes") or {}
        bylaw = clean(rec.get("Bylaw_No"))
        if bylaw is None:
            raise RuntimeError(f"Welland heritage row without a by-law number: {rec}")
        if bylaw in seen:
            raise RuntimeError(f"Welland heritage by-law {bylaw} repeated")
        seen.add(bylaw)
        street = clean(rec.get("Address"))
        label = clean(rec.get("CommonName")) or street
        wkt = esri_geometry_to_wkt(feature.get("geometry"))
        if label is None or wkt is None:
            skipped += 1
            continue
        landmark_id.append(f"wld-hp-{bylaw}")
        name.append(label)
        geometry_raw.append(wkt)
        address.append(street)
        year_built.append(year(rec.get("Year_built")))
        year_designated.append(year(rec.get("Year_Desig")))

    if skipped:
        print(f"Welland landmarks: {skipped} row(s) without a name or geometry", file=sys.stderr)
    n = len(landmark_id)
    return pa.table(
        {
            "landmark_id": pa.array(landmark_id, type=pa.string()),
            "city": pa.array(["CAWLD"] * n, type=pa.string()),
            "name": pa.array(name, type=pa.string()),
            "geometry_raw": pa.array(geometry_raw, type=pa.string()),
            "address": pa.array(address, type=pa.string()),
            "year_built": pa.array(year_built, type=pa.int64()),
            "year_designated": pa.array(year_designated, type=pa.int64()),
            "property_type": pa.array([PROPERTY_TYPE] * n, type=pa.string()),
        }
    )


if __name__ == "__main__":
    features: list[dict] = []
    for page in iter_features(
        LAYER, out_fields=OUT_FIELDS, return_geometry=True, out_sr=4326
    ):
        features.extend(page)
    table = transform(features)
    print(
        f"Welland landmarks: {table.num_rows:,} of {len(features):,} register rows",
        file=sys.stderr,
    )
    if table.num_rows == 0:
        raise RuntimeError("Welland heritage register produced no landmarks")
    emit(table)
