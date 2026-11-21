#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Fareham's nationally designated heritage: listed buildings, scheduled
monuments and registered parks and gardens.

Source: Historic England's National Heritage List for England, the statutory
register, as published on its own ArcGIS Online organisation:
`National_Heritage_List_for_England_NHLE_v02_VIEW/FeatureServer` (item
767f279327a24845bf47dfe5eae9862b, owner `gis_historicengland`, "Data is
updated daily").  The item's access information reads "(c) Crown Copyright
2026. Contains Ordnance Survey data (c) Crown copyright and database right
2026. Released under OGL." and its licence links Historic England's Open
Data Hub terms.  Three of its layers are read:

* 0 `Listed Building points` (multipoints: one entry drawn as several parts
  is one row here, placed at the mean of its points), `lm_type`
  `Grade I/II*/II listed building`;
* 6 `Scheduled Monuments` (polygons), `lm_type` `Scheduled monument`;
* 7 `Parks and Gardens` (polygons), `lm_type` `Grade I/II*/II registered
  park and garden` -- read so that a future registration is picked up,
  though the borough has none on the register today.

The list has no local-authority column, so the city is cut out of it by
geometry: the layers are read inside the city boundary's bounding box and a
row is kept when its point (or at least half its polygon's vertices) falls
inside the boundary, which is the Office for National Statistics' Local
Authority Districts (May 2024) generalised boundary for Fareham
(E07000087), read live (OGL, "Source: Office for National Statistics
licensed under the Open Government Licence v.3.0; contains OS data (c) Crown
copyright and database right").  The district is the whole borough, the same
area as the council's tree layer.  439 entries on 2026-11-21: 434 listed
buildings (4 Grade I, 20 Grade II*, 410 Grade II) and 5 scheduled monuments
(Portchester Castle, Fort Fareham, Titchfield Abbey and its fishponds, Stony
Bridge at Titchfield, the Monument Farm anti-aircraft gunsite).  The code is
Basingstoke's (`gbbsk/basingstoke_landmarks.py`) with the district code
changed.

`landmark_id` is `fareham-{ListEntry}`, the NHLE list-entry number (unique
across every NHLE designation type), also published as `register_number`.
`year_designated` is the year first listed, scheduled or registered.  Names
written entirely in capitals (the pre-2010 entries: `CHURCH OF ST MARY`) are
title-cased.  Building Preservation Notices and Certificates of Immunity are
not designations and are not read.
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_geometry_to_wkt, iter_features
from shared.ingest import emit, get_json_with_retry, make_point_wkt

NHLE = (
    "https://services-eu1.arcgis.com/ZOdPfBS3aqqDYPUQ/arcgis/rest/services/"
    "National_Heritage_List_for_England_NHLE_v02_VIEW/FeatureServer"
)
LISTED = FeatureLayer(f"{NHLE}/0")
SCHEDULED = FeatureLayer(f"{NHLE}/6")
PARKS = FeatureLayer(f"{NHLE}/7")

BOUNDARY = FeatureLayer(
    "https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services/"
    "Local_Authority_Districts_May_2024_Boundaries_UK_BGC/FeatureServer/0"
)
LAD_CODE = "E07000087"

COLUMNS = ("landmark_id", "name", "geometry_raw", "lm_type", "register_number", "year_designated")

_SMALL_WORDS = frozenset({"a", "an", "and", "at", "by", "for", "in", "of", "on", "the", "to", "with"})
_ROMAN = frozenset({"I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X"})


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def _title_word(word: str, first: bool) -> str:
    if any(ch.isdigit() for ch in word) or word in _ROMAN:
        return word
    lower = word.lower()
    if not first and lower in _SMALL_WORDS:
        return lower
    # "ST.", "MARY'S", "NORTH-EAST" -> "St.", "Mary's", "North-East".
    return "-".join(part[:1].upper() + part[1:] for part in lower.split("-"))


def format_name(raw) -> str | None:
    """Title-case a name written entirely in capitals; leave mixed case alone."""
    text = clean(raw)
    if text is None or text != text.upper():
        return text
    return " ".join(_title_word(w, i == 0) for i, w in enumerate(text.split()))


def esri_year(value) -> int | None:
    """The year of an Esri epoch-milliseconds date, pre-1970 included."""
    if value in (None, ""):
        return None
    try:
        return (datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(milliseconds=float(value))).year
    except (OverflowError, ValueError, TypeError):
        return None


def boundary_rings() -> list[list[list[float]]]:
    """Fareham's district boundary as WGS84 rings."""
    payload = get_json_with_retry(
        BOUNDARY.query_url,
        params={
            "where": f"LAD24CD='{LAD_CODE}'",
            "outFields": "LAD24CD,LAD24NM",
            "returnGeometry": "true",
            "outSR": "4326",
            "f": "json",
        },
        timeout=BOUNDARY.timeout,
    )
    features = payload.get("features") or []
    if len(features) != 1:
        raise RuntimeError(f"expected one {LAD_CODE} boundary from {BOUNDARY.base}, got {len(features)}")
    rings = (features[0].get("geometry") or {}).get("rings") or []
    if not rings:
        raise RuntimeError(f"{LAD_CODE} boundary from {BOUNDARY.base} has no rings")
    return rings


def inside(lon: float, lat: float, rings) -> bool:
    """Even-odd point-in-polygon over every ring (holes included)."""
    hit = False
    for ring in rings:
        for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
            if (y1 > lat) != (y2 > lat) and lon < x1 + (lat - y1) * (x2 - x1) / (y2 - y1):
                hit = not hit
    return hit


def envelope(rings) -> dict[str, str]:
    xs = [p[0] for ring in rings for p in ring]
    ys = [p[1] for ring in rings for p in ring]
    return {
        "geometry": f"{min(xs)},{min(ys)},{max(xs)},{max(ys)}",
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
    }


def fetch_all(layer: FeatureLayer, fields: str, rings) -> list[dict]:
    return [
        feature
        for page in iter_features(
            layer, out_fields=fields, return_geometry=True, out_sr=4326,
            extra_params=envelope(rings),
        )
        for feature in page
    ]


def mean_point(geometry: dict | None) -> tuple[float, float] | None:
    """`(lon, lat)` of a point or the mean of a multipoint's points."""
    geometry = geometry or {}
    if geometry.get("x") is not None and geometry.get("y") is not None:
        return float(geometry["x"]), float(geometry["y"])
    points = [p for p in geometry.get("points") or [] if p and len(p) >= 2]
    if not points:
        return None
    return sum(p[0] for p in points) / len(points), sum(p[1] for p in points) / len(points)


def mostly_inside(geometry: dict | None, rings) -> bool:
    vertices = [p for ring in (geometry or {}).get("rings") or [] for p in ring]
    if not vertices:
        return False
    return sum(inside(p[0], p[1], rings) for p in vertices) * 2 >= len(vertices)


def build() -> pa.Table:
    rings = boundary_rings()
    rows: dict[str, list] = {k: [] for k in COLUMNS}
    seen: set[int] = set()

    def add(entry, name, wkt, lm_type, date_ms) -> None:
        if entry is None or name is None or wkt is None or int(entry) in seen:
            return
        seen.add(int(entry))
        rows["landmark_id"].append(f"fareham-{int(entry)}")
        rows["name"].append(name)
        rows["geometry_raw"].append(wkt)
        rows["lm_type"].append(lm_type)
        rows["register_number"].append(str(int(entry)))
        rows["year_designated"].append(esri_year(date_ms))

    listed = fetch_all(LISTED, "OBJECTID,ListEntry,Name,Grade,ListDate", rings)
    if not listed:
        raise RuntimeError(f"{LISTED.base} returned no listed buildings around Fareham")
    for feature in sorted(listed, key=lambda f: f["attributes"].get("ListEntry") or 0):
        attrs = feature.get("attributes") or {}
        point = mean_point(feature.get("geometry"))
        if point is None or not inside(point[0], point[1], rings):
            continue
        grade = clean(attrs.get("Grade"))
        add(
            attrs.get("ListEntry"),
            format_name(attrs.get("Name")),
            make_point_wkt(point[0], point[1]),
            f"Grade {grade} listed building" if grade else "Listed building",
            attrs.get("ListDate"),
        )

    for layer, fields, date_field, label in (
        (SCHEDULED, "OBJECTID,ListEntry,Name,SchedDate", "SchedDate", None),
        (PARKS, "OBJECTID,ListEntry,Name,Grade,RegDate", "RegDate", "registered park and garden"),
    ):
        for feature in sorted(
            fetch_all(layer, fields, rings), key=lambda f: f["attributes"].get("ListEntry") or 0
        ):
            attrs = feature.get("attributes") or {}
            if not mostly_inside(feature.get("geometry"), rings):
                continue
            if label is None:
                lm_type = "Scheduled monument"
            else:
                grade = clean(attrs.get("Grade"))
                lm_type = f"Grade {grade} {label}" if grade else label.capitalize()
            add(
                attrs.get("ListEntry"),
                format_name(attrs.get("Name")),
                esri_geometry_to_wkt(feature.get("geometry")),
                lm_type,
                attrs.get(date_field),
            )

    n = len(rows["landmark_id"])
    print(f"Fareham landmarks: {n} NHLE entries inside {LAD_CODE}", file=sys.stderr)
    return pa.table(
        {
            "landmark_id": pa.array(rows["landmark_id"], type=pa.string()),
            "name": pa.array(rows["name"], type=pa.string()),
            "city": pa.array(["GBFAR"] * n, type=pa.string()),
            "geometry_raw": pa.array(rows["geometry_raw"], type=pa.string()),
            "lm_type": pa.array(rows["lm_type"], type=pa.string()),
            "register_number": pa.array(rows["register_number"], type=pa.string()),
            "year_designated": pa.array(rows["year_designated"], type=pa.int64()),
        }
    )


if __name__ == "__main__":
    emit(build())
