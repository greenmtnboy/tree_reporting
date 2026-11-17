#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Hamburg's listed monuments, from the city's heritage-register WFS.

Source: `geodienste.hamburg.de/HH_WFS_Denkmalschutz`, the Denkmalkartierung
(heritage map) of the Denkmalliste kept by the Denkmalschutzamt under
Hamburg's Denkmalschutzgesetz -- the official designation register, catalogued
on transparenz.hamburg.de as "Denkmalkartierung Baudenkmale Hamburg"
(metadata id 3B43E143-2C8B-43E8-8004-EE9EDA3EA563) under the Datenlizenz
Deutschland -- Namensnennung -- 2.0, the same terms as the tree cadastre.

Three of the service's feature types are read, each a category:

* `flaechen_gartendenkmale` -- listed parks, gardens, cemeteries and avenues
  (`Hammer Park`, `Alter Botanischer Garten`), 139 polygons: the landmarks a
  tree is most often *in*;
* `denkmalobjekte_symbolhaft` -- statues, memorials, fountains and cranes,
  561 points (`Kaiser-Wilhelm-Denkmal in den Großen Wallanlagen`);
* `gebaeude` -- listed buildings, 12,286 polygons, **grouped by name**: the
  register draws a housing estate or a complex as one polygon per house
  (`Fritz-Schumacher-Siedlung` on 558 of them, `Großmarkthalle` on 7), so the
  polygons sharing a name publish as one landmark, a MULTIPOLYGON of them all.

A row is kept only when it has a name of its own.  The register names most
buildings by their address (`Haakestraße 30`) or not at all; like
Copenhagen's listed buildings, an address is a designation rather than a
landmark anyone asks for by name, so a `bezeichnung` ending in a house number
(or a number range) is dropped, as is a name that is only a building type (`Wohnhaus`) or
`Keine Angabe`.  Buildings marked `NK` (nicht konstituierend:
inside an ensemble but not itself a monument) are dropped too.  Ensembles are
left out: they overlap the buildings and gardens they group.  Centroids
outside the city's bounding box (the Neuwerk island exclave in the Elbe
estuary) are dropped, since no tree in the cadastre is there.

**Gardens and monuments are keyed on `globalid`**, the register's GUID:
unique and non-null over every layer read (`fisid`, the register's object
number, repeats when one listed object is drawn as several polygons).  **A
building group is keyed on a slug of its name** (`hamburg-building-fritz-
schumacher-siedlung`): the name is what defines the group, so the id moves only
when the group does, whereas the smallest `globalid` in it would change
whenever that one house was redrawn or delisted.  Spellings that slug alike
(`'Wikingerhaus'` and `Wikingerhaus`) are one group.
"""

import re
import sys
from collections import Counter
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import CITY_BOUNDS, emit, get_json_with_retry
from shared.platforms.wfs import geojson_to_wkt

SOURCE_URL = "https://geodienste.hamburg.de/HH_WFS_Denkmalschutz"
TIMEOUT = 300
# (feature type, id slug, category, group the polygons that share a name)
LAYERS = (
    ("flaechen_gartendenkmale", "garden", "Garden monument", False),
    ("denkmalobjekte_symbolhaft", "monument", "Monument", False),
    ("gebaeude", "building", "Listed building", True),
)
_FOLD = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})
# A name that ends in a house number, or carries a number range: an address.
_ADDRESS = re.compile(r"\d+\s*[a-zA-Z]?\s*$|\d+\s*[a-zA-Z]?\s*[/-]\s*\d")


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def read_layer(type_name: str) -> list[dict]:
    payload = get_json_with_retry(
        SOURCE_URL,
        params={
            "SERVICE": "WFS",
            "VERSION": "2.0.0",
            "REQUEST": "GetFeature",
            "TYPENAMES": f"de.hh.up:{type_name}",
            "SRSNAME": "EPSG:4326",
            "outputFormat": "application/geo+json",
        },
        timeout=TIMEOUT,
    )
    features = payload.get("features")
    if not features:
        raise RuntimeError(f"Hamburg heritage register: {type_name} returned no features")
    return features


def _first_point(geometry: dict) -> tuple[float, float] | None:
    coords = geometry.get("coordinates")
    while isinstance(coords, list) and coords and isinstance(coords[0], list):
        coords = coords[0]
    if isinstance(coords, list) and len(coords) >= 2:
        return float(coords[0]), float(coords[1])
    return None


def name_slug(name: str) -> str:
    """`Großmarkthalle` -> `grossmarkthalle`: ASCII, lower case, hyphenated."""
    text = name.casefold().translate(_FOLD)
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def polygons(geometry: dict) -> list:
    """A Polygon or MultiPolygon's polygons, as GeoJSON coordinate lists."""
    kind = geometry.get("type")
    coords = geometry.get("coordinates") or []
    if kind == "Polygon":
        return [coords]
    if kind == "MultiPolygon":
        return list(coords)
    return []


def grouped_records(groups: dict[str, list], slug: str, category: str) -> list[dict]:
    """One landmark per name slug, its polygons gathered into one MULTIPOLYGON.

    *groups* maps a name slug to `(name, polygons)` pairs.  Spellings that
    differ only in case, quoting or punctuation (`'Wikingerhaus'`,
    `Wikingerhaus`) share a slug and are one landmark, named by the spelling
    most of its polygons carry.
    """
    records: list[dict] = []
    for key_slug in sorted(groups):
        members = groups[key_slug]
        spellings = Counter(name for name, _ in members)
        name = min(spellings, key=lambda n: (-spellings[n], n))
        parts = [polygon for _, polys in members for polygon in polys]
        wkt = geojson_to_wkt({"type": "MultiPolygon", "coordinates": parts})
        records.append(
            {
                "landmark_id": f"hamburg-{slug}-{key_slug}",
                "name": name,
                "geometry_raw": wkt,
                "category": category,
            }
        )
    return records


def read_landmarks() -> list[dict]:
    south, north, west, east = CITY_BOUNDS["DEHAM"]
    records: list[dict] = []
    seen: set[str] = set()
    for type_name, slug, category, group in LAYERS:
        groups: dict[str, list] = {}
        dropped = {
            "unnamed": 0, "address": 0, "generic": 0,
            "not_constituent": 0, "outside": 0, "unusable": 0,
        }
        kept = 0
        features = read_layer(type_name)
        # A name that is only a building type (`Wohnhaus`, `Wohnwirtschaftsgebäude`)
        # or `Keine Angabe` (not stated) names nothing.
        generic = {"keine angabe"} | {
            part.strip().casefold()
            for f in features
            for part in str((f.get("properties") or {}).get("bautyp") or "").split(";")
            if part.strip()
        }
        for feature in features:
            props = feature.get("properties") or {}
            geometry = feature.get("geometry") or {}
            guid = clean(props.get("globalid"))
            name = clean(props.get("bezeichnung"))
            wkt = geojson_to_wkt(geometry)
            point = _first_point(geometry)
            if guid is None or wkt is None or point is None:
                dropped["unusable"] += 1
                continue
            if (props.get("kdstatus") or "KD") != "KD":
                dropped["not_constituent"] += 1
                continue
            if name is None:
                dropped["unnamed"] += 1
                continue
            if _ADDRESS.search(name):
                dropped["address"] += 1
                continue
            if name.casefold() in generic:
                dropped["generic"] += 1
                continue
            lon, lat = point
            if not (south <= lat <= north and west <= lon <= east):
                dropped["outside"] += 1
                continue
            if group:
                parts = polygons(geometry)
                if not parts:
                    dropped["unusable"] += 1
                    continue
                key_slug = name_slug(name)
                if not key_slug:
                    dropped["unnamed"] += 1
                    continue
                groups.setdefault(key_slug, []).append((name, parts))
                kept += 1
                continue
            key = f"hamburg-{slug}-{guid.strip('{}').lower()}"
            if key in seen:
                raise RuntimeError(f"Hamburg heritage register: globalid {guid} repeats")
            seen.add(key)
            records.append(
                {"landmark_id": key, "name": name, "geometry_raw": wkt, "category": category}
            )
            kept += 1
        if group:
            grouped = grouped_records(groups, slug, category)
            seen.update(r["landmark_id"] for r in grouped)
            records.extend(grouped)
            print(
                f"Hamburg {type_name}: {kept} kept as {len(grouped)} named landmark(s), "
                f"dropped {dropped}",
                file=sys.stderr,
            )
            continue
        print(f"Hamburg {type_name}: {kept} kept, dropped {dropped}", file=sys.stderr)
    return records


def transform(records: list[dict]) -> pa.Table:
    return pa.table(
        {
            "landmark_id": pa.array([r["landmark_id"] for r in records], type=pa.string()),
            "city": pa.array(["DEHAM"] * len(records), type=pa.string()),
            "name": pa.array([r["name"] for r in records], type=pa.string()),
            "geometry_raw": pa.array([r["geometry_raw"] for r in records], type=pa.string()),
            "category": pa.array([r["category"] for r in records], type=pa.string()),
        }
    )


if __name__ == "__main__":
    emit(transform(read_landmarks()))
