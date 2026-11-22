#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Surrey's Heritage Register.

Source: "Heritage Sites" on the City of Surrey's ArcGIS Online organisation
(item b65ea74c6e4743928ae25392935ca08f, owner SurreyGIS, listed on the City's
open data catalogue opendata-surrey.hub.arcgis.com): the sites on the Surrey
Heritage Register, 208 points on 2026-11-22, each with the register's site
number, its name and address, the heritage neighbourhood, the date of the
Council resolution that registered it and the kind of heritage value.
`TYPE` says whether a site is only `Registered` (132) or also `Registered
and Protected` (76, a heritage designation by-law or revitalization
agreement).  Same licence as the trees: the Open Government License - Surrey
(https://opendata-surrey.hub.arcgis.com/pages/55089a19491a4fe59a41e059fd8af708).

This is the runbook's first-preference landmark source: the City's own
statutory register, read live, no geocoding and nothing to stage.

**The id is the register's `SITE_NO`** (`sry-{SITE_NO}`) where it names one
row.  Twelve rows carry no site number and two share one (site 218, a pair of
addresses on Old McLellan Road); those take their rounded position
(`sry-{lon}_{lat}`, 6 dp).  The layer has no GlobalID and its `OBJECTID` is a
dense 1..N.

**A register entry is not always named.**  A handful are called just `House`
or carry their address as the name; those are named by address, and a row
with neither name nor address is skipped.
"""

import sys
from collections import Counter
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import (
    FeatureLayer,
    esri_geometry_to_wkt,
    esri_ms_to_datetime,
    esri_point,
    iter_features,
)
from shared.ingest import emit

LAYER = FeatureLayer(
    "https://services5.arcgis.com/YRpe0VKTJytZSSIB/arcgis/rest/services/"
    "Heritage%20Sites/FeatureServer/0",
    timeout=120,
)

OUT_FIELDS = (
    "OBJECTID,TYPE,SITE_NO,SITE_NAME,ADDRESS,HERITAGE_NEIGHBOURHOOD,"
    "REGISTER_RESOLUTION_DATE,HERITAGE_VALUE"
)

# Names the register uses for an unnamed building.
GENERIC_NAMES = frozenset({"house", "residence", "building"})


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def site_number(value) -> str | None:
    if value in (None, ""):
        return None
    number = float(value)
    return str(int(number)) if number.is_integer() else None


def label_for(name: str | None, address: str | None) -> str | None:
    if name is None or name.lower() in GENERIC_NAMES:
        if address is None:
            return name
        return f"{name}, {address}" if name else address
    return name


def heritage_value(value) -> str | None:
    """`Architectural  Historical` -> `Architectural, Historical`."""
    text = clean(value)
    if text is None:
        return None
    words = [w for w in text.replace(" and ", " ").split() if w]
    return ", ".join(words)


def transform(features: list[dict]) -> pa.Table:
    numbers = Counter(
        site_number((f.get("attributes") or {}).get("SITE_NO")) for f in features
    )
    landmark_id: list[str] = []
    name: list[str] = []
    geometry_raw: list[str] = []
    address: list[str | None] = []
    year_designated: list[int | None] = []
    property_type: list[str | None] = []
    category: list[str | None] = []
    neighborhood: list[str | None] = []
    skipped: Counter[str] = Counter()

    for feature in features:
        rec = feature.get("attributes") or {}
        street = clean(rec.get("ADDRESS"))
        label = label_for(clean(rec.get("SITE_NAME")), street)
        if label is None:
            skipped["no name or address"] += 1
            continue
        geometry = feature.get("geometry")
        wkt = esri_geometry_to_wkt(geometry)
        lat, lon = esri_point(geometry)
        if wkt is None or lat is None or lon is None:
            skipped["no geometry"] += 1
            continue
        number = site_number(rec.get("SITE_NO"))
        if number is not None and numbers[number] == 1:
            landmark_id.append(f"sry-{number}")
        else:
            landmark_id.append(f"sry-{lon:.6f}_{lat:.6f}")
        name.append(label)
        geometry_raw.append(wkt)
        address.append(street)
        resolved = esri_ms_to_datetime(rec.get("REGISTER_RESOLUTION_DATE"))
        year_designated.append(resolved.year if resolved else None)
        property_type.append(clean(rec.get("TYPE")))
        category.append(heritage_value(rec.get("HERITAGE_VALUE")))
        neighborhood.append(clean(rec.get("HERITAGE_NEIGHBOURHOOD")))

    if len(set(landmark_id)) != len(landmark_id):
        repeated = [k for k, v in Counter(landmark_id).items() if v > 1]
        raise RuntimeError(f"Surrey landmarks: repeated landmark_id {repeated[:5]}")
    if skipped:
        print(
            "Surrey landmarks: skipped "
            + ", ".join(f"{v} {k}" for k, v in sorted(skipped.items())),
            file=sys.stderr,
        )

    n = len(landmark_id)
    return pa.table(
        {
            "landmark_id": pa.array(landmark_id, type=pa.string()),
            "city": pa.array(["CASRY"] * n, type=pa.string()),
            "name": pa.array(name, type=pa.string()),
            "geometry_raw": pa.array(geometry_raw, type=pa.string()),
            "address": pa.array(address, type=pa.string()),
            "year_designated": pa.array(year_designated, type=pa.int64()),
            "property_type": pa.array(property_type, type=pa.string()),
            "category": pa.array(category, type=pa.string()),
            "neighborhood": pa.array(neighborhood, type=pa.string()),
        }
    )


if __name__ == "__main__":
    features: list[dict] = []
    for page in iter_features(
        LAYER, out_fields=OUT_FIELDS, return_geometry=True, out_sr=4326
    ):
        features.extend(page)
    emit(transform(features))
