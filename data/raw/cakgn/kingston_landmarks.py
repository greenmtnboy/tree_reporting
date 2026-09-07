#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Kingston's designated heritage sites.

Source: "Designated Heritage Site" (`Planning/DesignatedHeritageSite`) on the
city's own GIS server, linked from `opendatakingston.cityofkingston.ca` --
properties designated under Part IV or Part V of the Ontario Heritage Act,
about 1,235 of them, each with the parcel, the designating by-law and the year
of construction.

This is the runbook's first-preference landmark source: an official
designation registry, read live, no geocoding and no staging object.  Kingston
publishes three plausible alternatives and this is the one that matches what
Montreal and Quebec City took in the same position -- the statutory register.
`Historic_Monuments` is 88 war memorials and plaques, a subset by another
name; the `Landmark` layer is 242 civic buildings (schools, arenas,
cemeteries) and is not a heritage register at all.

**A designated property is not always named.**  `NOTES_NAME_OF_BLDG` carries a
real name where the register has one ("Milton Cemetery", "Murray House") and
is empty for the many designated houses that are known only by their address,
so the address is the fallback.  A row with neither is skipped: `name` is what
the map and the agent read, and a blank one names no ground.

**De-designated and demolished properties are dropped.**  The register keeps
them with a flag rather than deleting the row, which is right for a register
and wrong for a map.
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
    "https://api.cityofkingston.ca/gis_unfed/rest/services/Planning/"
    "DesignatedHeritageSite/FeatureServer/4",
    timeout=180,
)

OUT_FIELDS = (
    "GLOBALID,HERITAGE_SITE_ID,NOTES_NAME_OF_BLDG,ADDRESS,"
    "DATE_OF_CONSTRUCTION,OHA_DESIGNATION,OHA_BY_LAW,DEMOLISHED,DE_DESIGNATED"
)


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def parse_year(value) -> int | None:
    """The leading four-digit year of a construction-date string, or None.

    `DATE_OF_CONSTRUCTION` is free text and carries qualifiers and ranges
    ("1874 (circa)", "1820"), so take the first year mentioned rather than
    requiring the whole value to be a number.
    """
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


def is_current(rec: dict) -> bool:
    """False for a property the register records as gone or un-designated."""
    return not (rec.get("DEMOLISHED") == 1 or rec.get("DE_DESIGNATED") == 1)


def transform(features: list[dict]) -> pa.Table:
    landmark_id: list[str] = []
    city: list[str] = []
    name: list[str] = []
    geometry_raw: list[str | None] = []
    address: list[str | None] = []
    year_built: list[int | None] = []
    year_designated: list[int | None] = []
    property_type: list[str | None] = []

    for feature in features:
        rec = feature.get("attributes") or {}
        if not is_current(rec):
            continue
        street = clean(rec.get("ADDRESS"))
        label = clean(rec.get("NOTES_NAME_OF_BLDG")) or street
        if label is None:
            continue
        wkt = esri_geometry_to_wkt(feature.get("geometry"))
        if wkt is None:
            continue
        raw_id = str(rec.get("GLOBALID") or "").strip()
        if not raw_id:
            continue

        landmark_id.append(f"kgn-{raw_id}")
        city.append("CAKGN")
        name.append(label)
        geometry_raw.append(wkt)
        address.append(street)
        year_built.append(parse_year(rec.get("DATE_OF_CONSTRUCTION")))
        designated = esri_ms_to_datetime(rec.get("OHA_DESIGNATION"))
        year_designated.append(designated.year if designated else None)
        # The by-law that designated it -- Kingston's equivalent of Paris's
        # `protection_type`, and the only citation the register offers.
        property_type.append(clean(rec.get("OHA_BY_LAW")))

    return pa.table(
        {
            "landmark_id": pa.array(landmark_id, type=pa.string()),
            "city": pa.array(city, type=pa.string()),
            "name": pa.array(name, type=pa.string()),
            "geometry_raw": pa.array(geometry_raw, type=pa.string()),
            "address": pa.array(address, type=pa.string()),
            "year_built": pa.array(year_built, type=pa.int64()),
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
