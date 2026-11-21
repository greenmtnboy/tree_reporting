#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Edinburgh's nationally designated heritage: listed buildings, scheduled
monuments and gardens and designed landscapes.

Source: Historic Environment Scotland's designations, the statutory
registers, as published on HES's own INSPIRE ArcGIS Server:
`inspire.hes.scot/arcgis/rest/services/HES/HES_Designations/MapServer`.
HES's terms (https://portal.historicenvironment.scot/termsandconditions):
all its spatial downloads except Historic Landuse Assessment data are
available under the Open Government Licence, cited as "Contains Historic
Environment Scotland and Ordnance Survey data (c) Historic Environment
Scotland - Scottish Government; (c) Crown copyright and database right
[year]".  Three layers are read:

* 0 `Listed Buildings by Category` (points; one listing drawn at several
  addresses is several rows sharing a `DES_REF`, published here as one row
  at the mean of its points), `lm_type` `Category A/B/C listed building`;
* 5 `Scheduled Monuments` (polygons), `lm_type` `Scheduled monument`;
* 4 `Gardens and Designed Landscapes` (polygons) -- the Inventory, which in
  Edinburgh is the Royal Botanic Garden, the New Town Gardens, Holyrood,
  Dean Cemetery, Lauriston, Dalmeny and the like; `lm_type` `Garden and
  designed landscape`.

The city is HES's own `LOCAL_AUTH = 'Edinburgh'` (no entry carries a
compound authority).  2026-11-20: 4,886 landmarks -- 4,813 listings (895
Category A, 2,603 B, 1,315 C) from 10,111 listed-building points, 56
scheduled monuments, 17 inventory gardens.  The service does not page (`resultOffset`
is refused), so each layer is read by its object ids in FID ranges.

`landmark_id` is `edinburgh-{DES_REF}` (`LB27677`, `SM6188`, `GDL00334`:
unique across the three registers), also published as `register_number`.
`year_designated` is the year of `DESIGNATED`.  Names written entirely in
capitals (most listed buildings: `12-16 (INCLUSIVE NOS) BANK STREET`) are
title-cased.  Conservation areas and the World Heritage Site are areas, not
landmarks, and are not read.
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_geometry_to_wkt
from shared.ingest import emit, get_json_with_retry, make_point_wkt

HES = "https://inspire.hes.scot/arcgis/rest/services/HES/HES_Designations/MapServer"
LISTED = FeatureLayer(f"{HES}/0", timeout=180)
GARDENS = FeatureLayer(f"{HES}/4", timeout=180)
SCHEDULED = FeatureLayer(f"{HES}/5", timeout=180)

WHERE = "LOCAL_AUTH = 'Edinburgh'"
ID_FIELD = "FID"
# Object ids per request: well under the layers' 10,000 maxRecordCount.
CHUNK = 2000

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


def object_ids(layer: FeatureLayer) -> list[int]:
    payload = get_json_with_retry(
        layer.query_url,
        params={"where": WHERE, "returnIdsOnly": "true", "f": "json"},
        timeout=layer.timeout,
    )
    ids = payload.get("objectIds")
    if ids is None:
        raise RuntimeError(f"no objectIds from {layer.base}: {str(payload)[:200]}")
    return sorted(int(i) for i in ids)


def fetch_all(layer: FeatureLayer, fields: str, *, geometry: bool = True) -> list[dict]:
    """Every Edinburgh feature of *layer*, read in FID ranges (the server will not page)."""
    ids = object_ids(layer)
    features: list[dict] = []
    for start in range(0, len(ids), CHUNK):
        chunk = ids[start:start + CHUNK]
        params = {
            "where": f"{WHERE} AND {ID_FIELD} >= {chunk[0]} AND {ID_FIELD} <= {chunk[-1]}",
            "outFields": f"{ID_FIELD},{fields}",
            "returnGeometry": "true" if geometry else "false",
            "f": "json",
        }
        if geometry:
            params["outSR"] = "4326"
        payload = get_json_with_retry(layer.query_url, params=params, timeout=layer.timeout)
        got = payload.get("features") or []
        if len(got) != len(chunk):
            raise RuntimeError(
                f"{layer.base}: FIDs {chunk[0]}-{chunk[-1]} returned {len(got)} "
                f"features, expected {len(chunk)}"
            )
        features.extend(got)
    return features


def build() -> pa.Table:
    rows: dict[str, list] = {k: [] for k in COLUMNS}

    def add(ref, name, wkt, lm_type, date_ms) -> None:
        rows["landmark_id"].append(f"edinburgh-{ref}")
        rows["name"].append(name)
        rows["geometry_raw"].append(wkt)
        rows["lm_type"].append(lm_type)
        rows["register_number"].append(ref)
        rows["year_designated"].append(esri_year(date_ms))

    # Listed buildings: several address points per listing, one row per listing.
    listings: dict[str, dict] = {}
    listed = fetch_all(LISTED, "DES_REF,DES_TITLE,CATEGORY,DESIGNATED")
    if not listed:
        raise RuntimeError(f"{LISTED.base} returned no listed buildings for Edinburgh")
    for feature in listed:
        attrs = feature.get("attributes") or {}
        geom = feature.get("geometry") or {}
        ref = clean(attrs.get("DES_REF"))
        if ref is None or geom.get("x") is None or geom.get("y") is None:
            continue
        entry = listings.setdefault(ref, {"attrs": attrs, "points": []})
        entry["points"].append((float(geom["x"]), float(geom["y"])))
    for ref in sorted(listings):
        entry = listings[ref]
        attrs, points = entry["attrs"], entry["points"]
        lon = sum(p[0] for p in points) / len(points)
        lat = sum(p[1] for p in points) / len(points)
        category = clean(attrs.get("CATEGORY"))
        add(
            ref,
            format_name(attrs.get("DES_TITLE")),
            make_point_wkt(lon, lat),
            f"Category {category} listed building" if category else "Listed building",
            attrs.get("DESIGNATED"),
        )

    seen = set(listings)
    for layer, lm_type in (
        (SCHEDULED, "Scheduled monument"),
        (GARDENS, "Garden and designed landscape"),
    ):
        features = fetch_all(layer, "DES_REF,DES_TITLE,DESIGNATED")
        for feature in sorted(features, key=lambda f: clean(f["attributes"].get("DES_REF")) or ""):
            attrs = feature.get("attributes") or {}
            ref = clean(attrs.get("DES_REF"))
            wkt = esri_geometry_to_wkt(feature.get("geometry"))
            if ref is None or wkt is None or ref in seen:
                continue
            seen.add(ref)
            add(ref, format_name(attrs.get("DES_TITLE")), wkt, lm_type, attrs.get("DESIGNATED"))

    n = len(rows["landmark_id"])
    print(
        f"Edinburgh landmarks: {n} HES designations ({len(listings)} listings from "
        f"{len(listed)} listed-building points)",
        file=sys.stderr,
    )
    return pa.table(
        {
            "landmark_id": pa.array(rows["landmark_id"], type=pa.string()),
            "name": pa.array(rows["name"], type=pa.string()),
            "city": pa.array(["GBEDI"] * n, type=pa.string()),
            "geometry_raw": pa.array(rows["geometry_raw"], type=pa.string()),
            "lm_type": pa.array(rows["lm_type"], type=pa.string()),
            "register_number": pa.array(rows["register_number"], type=pa.string()),
            "year_designated": pa.array(rows["year_designated"], type=pa.int64()),
        }
    )


if __name__ == "__main__":
    emit(build())
