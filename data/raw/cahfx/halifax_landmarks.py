#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Halifax's registered heritage properties.

Source: "Heritage Properties" (`Heritage_Properties`) on
`catalogue-hrm.opendata.arcgis.com` -- the municipal heritage register kept
under the Nova Scotia Heritage Property Act, 530 designated properties with a
name, the year the building went up and the parcel it sits on.

This is the runbook's first-preference landmark source: an official
designation registry, on the same portal as the trees, read live.  No
Nominatim geocoding, no committed CSV and no staging object -- only
Overpass-backed and hand-curated sources need those.

The geometry is the designated *parcel* rather than a point, which
`landmark_common` prefers: it derives the centroid it needs with
`geo_centroid`, and the outline is better context than a pin for a property
like the Halifax Citadel.  `esri_geometry_to_wkt` does the ring-to-WKT
conversion, grouping rings by orientation so a courtyard stays a hole.

HRM also publishes "Heritage Conservation Districts" and "RC Landmark Building
Sites" separately.  Neither is unioned in here, for the reason Denver's
districts are not: a district is an area containing many of these properties,
so including both double-counts the same places and drops a landmark pin in
the middle of a neighbourhood.
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
    "https://services2.arcgis.com/11XBiaBYA9Ep0yNJ/arcgis/rest/services/"
    "Heritage_Properties/FeatureServer/0"
)

OUT_FIELDS = "GLOBALID,HRTG_NM,HRTG_YEAR,REG_TYPE,REG_DATE"


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def parse_year(value) -> int | None:
    """The leading four-digit year of a HRTG_YEAR string, or None.

    The column is a string and carries ranges and qualifiers ("c. 1830",
    "1890-1900"), so take the first year mentioned rather than requiring the
    whole value to be a number.
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


def transform(features: list[dict]) -> pa.Table:
    landmark_id: list[str] = []
    city: list[str] = []
    name: list[str] = []
    geometry_raw: list[str | None] = []
    year_built: list[int | None] = []
    year_designated: list[int | None] = []
    property_type: list[str | None] = []

    for feature in features:
        rec = feature.get("attributes") or {}
        label = clean(rec.get("HRTG_NM"))
        if label is None:
            continue
        wkt = esri_geometry_to_wkt(feature.get("geometry"))
        if wkt is None:
            continue
        raw_id = str(rec.get("GLOBALID") or "").strip()
        if not raw_id:
            continue

        landmark_id.append(f"hfx-{raw_id}")
        city.append("CAHFX")
        name.append(label)
        geometry_raw.append(wkt)
        year_built.append(parse_year(rec.get("HRTG_YEAR")))
        registered = esri_ms_to_datetime(rec.get("REG_DATE"))
        year_designated.append(registered.year if registered else None)
        # BLD, STR, LSC ... what kind of thing was designated.
        property_type.append(clean(rec.get("REG_TYPE")))

    return pa.table(
        {
            "landmark_id": pa.array(landmark_id, type=pa.string()),
            "city": pa.array(city, type=pa.string()),
            "name": pa.array(name, type=pa.string()),
            "geometry_raw": pa.array(geometry_raw, type=pa.string()),
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
