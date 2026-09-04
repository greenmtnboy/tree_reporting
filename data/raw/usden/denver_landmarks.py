#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Denver's individually designated historic landmark structures.

Source: `ODC_HIST_LANDMARKSTRUCTURE_P` on the Denver geospatial hub -- the
Landmark Preservation Commission's register, ~372 points carrying the official
landmark number, name, address and construction year.

This is the first preference in the runbook's landmark source order (an
official designation registry, on the same portal as the trees), so Denver
needs neither the Nominatim geocoding path nor a committed CSV.  Overpass is
not involved either, so there is nothing to stage: the layer is small, the
refresh reads it directly, and the freshness probe is the layer's own edit
date.

Denver publishes the districts separately (`ODC_HIST_LANDMARKDISTRICT_A`), and
they are deliberately not unioned in here: a district is an area containing
many structures, so including both would double-count the same places and put
a landmark pin in the middle of a neighbourhood.  `HISTORIC_DIST` on each
structure already carries the district it sits in.
"""

import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from _arcgis_shared import FeatureLayer, iter_features
from _ingest_shared import emit, make_point_wkt

LAYER = FeatureLayer(
    "https://services1.arcgis.com/zdB7qR0BtYrg0Xpl/arcgis/rest/services/"
    "ODC_HIST_LANDMARKSTRUCTURE_P/FeatureServer/69"
)

OUT_FIELDS = "LDMK_NUM,LDMK_NAME,AKA_NAME,ADDRESS_LINE1,YEAR_BUILT,HISTORIC_DIST,ORD_YEAR"


def parse_year(value) -> int | None:
    """The leading four-digit year of a YEAR_BUILT string, or None.

    Denver writes ranges and qualifiers into this field ("1888-92", "c. 1901"),
    so take the first year mentioned rather than requiring the whole value to
    be a number.
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


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def transform(features: list[dict]) -> pa.Table:
    landmark_id: list[str] = []
    name: list[str] = []
    city: list[str] = []
    geometry_raw: list[str | None] = []
    address: list[str | None] = []
    neighborhood: list[str | None] = []
    year_built: list[int | None] = []
    year_designated: list[int | None] = []

    for feature in features:
        attrs = feature.get("attributes") or {}
        geom = feature.get("geometry") or {}

        number = attrs.get("LDMK_NUM")
        if number is None:
            continue
        wkt = make_point_wkt(geom.get("x"), geom.get("y"))
        if wkt is None:
            continue
        label = clean(attrs.get("LDMK_NAME")) or clean(attrs.get("AKA_NAME"))
        if not label:
            continue

        landmark_id.append(f"den-{number}")
        name.append(label)
        city.append("USDEN")
        geometry_raw.append(wkt)
        address.append(clean(attrs.get("ADDRESS_LINE1")))
        neighborhood.append(clean(attrs.get("HISTORIC_DIST")))
        year_built.append(parse_year(attrs.get("YEAR_BUILT")))
        year_designated.append(parse_year(attrs.get("ORD_YEAR")))

    return pa.table(
        {
            "landmark_id": pa.array(landmark_id, type=pa.string()),
            "name": pa.array(name, type=pa.string()),
            "city": pa.array(city, type=pa.string()),
            "geometry_raw": pa.array(geometry_raw, type=pa.string()),
            "address": pa.array(address, type=pa.string()),
            "neighborhood": pa.array(neighborhood, type=pa.string()),
            "year_built": pa.array(year_built, type=pa.int64()),
            "year_designated": pa.array(year_designated, type=pa.int64()),
        }
    )


if __name__ == "__main__":
    features: list[dict] = []
    for page in iter_features(
        LAYER, out_fields=OUT_FIELDS, return_geometry=True, order_by="OBJECTID"
    ):
        features.extend(page)
    emit(transform(features))
