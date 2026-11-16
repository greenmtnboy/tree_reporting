#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Adelaide's heritage places: the SA Heritage Register and the City of
Adelaide's local heritage list, from the state's "SA Heritage Places" layer.

Source: "SA Heritage Places" (`sa-heritage-places`) on data.sa.gov.au,
published by the Department for Housing and Urban Development under CC BY
(the package says CC BY 4.0; the licence file inside the zip still says CC BY
3.0 AU).  It is the statewide register of designated places: State Heritage
Places (the South Australian Heritage Register) and every council's Local
Heritage Places, plus "Contributory" items, which are not designated places in
their own right but buildings that contribute to a historic area.  The ingest
reads the GDA2020 polygon GeoJSON from the 15 MB download zip.

This is the first preference in the runbook's landmark source order (an
official designation registry), so there is nothing to geocode or stage.

Measured 2026-11-16 (zip `Last-Modified` 2026-06-28): 2,000 polygon features
in the City of Adelaide LGA, of which 479 State and 1,476 Local features make
**1,933 places** (466 State, counting one Local place whose second class is
State, and 1,467 Local) once a place drawn as several polygons is merged into
one MULTIPOLYGON (19 are).  The 45 Contributory items are left out.

Field mapping:

  heritagenr                  -> landmark_id ("adl-h{heritagenr}"); the
                                 register database's place number, shared by
                                 every polygon of one place
  details                     -> name; when the same text names more than one
                                 place ("House" names 696) the address is
                                 appended, so the agent can tell them apart
  parlocation                 -> address
  heritageclass1/2            -> protection_type ("State Heritage Place" when
                                 either class is State, else "Local Heritage
                                 Place")
  shrcode                     -> register_number (the SA Heritage Register
                                 number; State places only)
  as2482desc                  -> category (the AS 2482 place type)
  geometry (MultiPolygon)     -> geometry_raw (WKT)
"""

import io
import json
import sys
import zipfile
from collections import Counter
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit, get_with_retry

# Kept in step with adelaide_landmarks_probe.py, which HEADs the same object.
SOURCE_URL = "https://www.dptiapps.com.au/dataportal/SAHeritagePlaces_geojson.zip"
MEMBER = "SAHeritagePlacesPoly_GDA2020.geojson"

LGA = "Adelaide"
CLASSES = {"S": "State Heritage Place", "L": "Local Heritage Place"}


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def ring_wkt(ring: list) -> str:
    return "(" + ", ".join(f"{pt[0]} {pt[1]}" for pt in ring) + ")"


def polygons_of(geometry: dict | None) -> list[list]:
    """A GeoJSON Polygon/MultiPolygon as a list of polygons (lists of rings)."""
    if not geometry:
        return []
    kind = geometry.get("type")
    coords = geometry.get("coordinates") or []
    if kind == "Polygon":
        return [coords] if coords else []
    if kind == "MultiPolygon":
        return [poly for poly in coords if poly]
    return []


def to_wkt(polygons: list[list]) -> str | None:
    if not polygons:
        return None
    parts = ["(" + ", ".join(ring_wkt(r) for r in poly) + ")" for poly in polygons]
    if len(parts) == 1:
        return f"POLYGON{parts[0]}"
    return f"MULTIPOLYGON({', '.join(parts)})"


def read_features() -> list[dict]:
    response = get_with_retry(SOURCE_URL, timeout=300)
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        if MEMBER not in archive.namelist():
            raise RuntimeError(
                f"{SOURCE_URL} no longer contains {MEMBER}: {archive.namelist()}"
            )
        payload = json.loads(archive.read(MEMBER))
    features = payload.get("features")
    if not features:
        raise RuntimeError(f"{MEMBER} in {SOURCE_URL} has no features")
    return features


def transform(features: list[dict]) -> pa.Table:
    places: dict[int, dict] = {}
    for feature in features:
        props = feature.get("properties") or {}
        if props.get("lgadesc") != LGA:
            continue
        classes = {props.get("heritageclass1"), props.get("heritageclass2")}
        if not classes & CLASSES.keys():
            continue
        number = props.get("heritagenr")
        if number is None:
            continue
        place = places.setdefault(
            number,
            {
                "details": clean(props.get("details")),
                "address": clean(props.get("parlocation")),
                "state": False,
                "register_number": None,
                "category": clean(props.get("as2482desc")),
                "polygons": [],
            },
        )
        place["state"] = place["state"] or "S" in classes
        if props.get("shrcode") is not None:
            place["register_number"] = str(props["shrcode"])
        place["polygons"].extend(polygons_of(feature.get("geometry")))

    if not places:
        raise RuntimeError(f"no State or Local heritage places in the {LGA} LGA")

    repeats = Counter(p["details"] for p in places.values())

    landmark_id, name, geometry_raw = [], [], []
    address, protection_type, register_number, category = [], [], [], []
    for number in sorted(places):
        place = places[number]
        label = place["details"] or place["address"]
        if label is None:
            continue
        if place["details"] and repeats[place["details"]] > 1 and place["address"]:
            label = f"{place['details']}, {place['address']}"
        landmark_id.append(f"adl-h{number}")
        name.append(label)
        geometry_raw.append(to_wkt(place["polygons"]))
        address.append(place["address"])
        protection_type.append(CLASSES["S"] if place["state"] else CLASSES["L"])
        register_number.append(place["register_number"])
        category.append(place["category"])

    return pa.table(
        {
            "landmark_id": pa.array(landmark_id, type=pa.string()),
            "city": pa.array(["AUADL"] * len(landmark_id), type=pa.string()),
            "name": pa.array(name, type=pa.string()),
            "geometry_raw": pa.array(geometry_raw, type=pa.string()),
            "address": pa.array(address, type=pa.string()),
            "protection_type": pa.array(protection_type, type=pa.string()),
            "register_number": pa.array(register_number, type=pa.string()),
            "category": pa.array(category, type=pa.string()),
        }
    )


if __name__ == "__main__":
    table = transform(read_features())
    print(f"Adelaide landmarks: {table.num_rows} heritage places", file=sys.stderr)
    emit(table)
