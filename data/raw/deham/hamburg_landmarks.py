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
* `gebaeude` -- listed buildings, 12,286 polygons, **grouped by name and
  place**: the register draws a housing estate or a complex as one polygon
  per house (`Fritz-Schumacher-Siedlung` on 558 of them, `Großmarkthalle` on
  7), so polygons sharing a name publish as one landmark, a MULTIPOLYGON --
  but a name is not a place: `Auferstehungskirche`, `Christuskirche` and
  `Lutherkirche` are each several churches up to 24 km apart.  The layer
  carries no district (`info` holds only the street address), so a name's
  polygons are split into clusters, single-linkage at 1 km between polygon
  centres: a contiguous estate or cemetery stays one landmark however long
  (the Ohlsdorf cemetery's 39 buildings span 3 km), while churches in
  different quarters part.

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
(`'Wikingerhaus'` and `Wikingerhaus`) are one group.  When a name forms more
than one cluster, each cluster's id adds the slug of its street -- the street
of the address in `info` most of its polygons carry
(`hamburg-building-auferstehungskirche-langenhorner-chaussee`) -- and a
running number only if two clusters of one name share a street, numbered
south to north.
"""

import math
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
CLUSTER_KM = 1.0
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


def centre(polys: list) -> tuple[float, float]:
    """The mean vertex of a feature's outer rings: a polygon's rough centre."""
    points = [pt for polygon in polys for pt in polygon[0]]
    return (
        sum(pt[0] for pt in points) / len(points),
        sum(pt[1] for pt in points) / len(points),
    )


def distance_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lon1, lat1, lon2, lat2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def clusters(members: list[dict]) -> list[list[dict]]:
    """Single-linkage clusters of *members* at `CLUSTER_KM` between centres."""
    parent = list(range(len(members)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(members)):
        for j in range(i + 1, len(members)):
            if distance_km(members[i]["centre"], members[j]["centre"]) <= CLUSTER_KM:
                parent[find(i)] = find(j)
    out: dict[int, list[dict]] = {}
    for i, member in enumerate(members):
        out.setdefault(find(i), []).append(member)
    return list(out.values())


def street_of(info: str | None) -> str | None:
    """`Gründgensstraße 28, Martin-Luther-King-Kirche (...)` -> `Gründgensstraße`."""
    first = clean((info or "").split(",")[0])
    if first is None:
        return None
    street = re.sub(r"\s+(\d.*|o\.\s*Nr\..*)$", "", first)
    return clean(street)


def grouped_records(groups: dict[str, list], slug: str, category: str) -> list[dict]:
    """One landmark per name slug and place, its polygons as one MULTIPOLYGON.

    *groups* maps a name slug to its member polygons (`name`, `polys`,
    `centre`, `street`).  Spellings that differ only in case, quoting or
    punctuation (`'Wikingerhaus'`, `Wikingerhaus`) share a slug; the slug's
    members are then split into clusters (`clusters`), and each cluster is
    named by the spelling most of its polygons carry.
    """
    records: list[dict] = []
    split = 0
    for key_slug in sorted(groups):
        parts_of_name = clusters(groups[key_slug])
        if len(parts_of_name) > 1:
            split += 1
        # South to north, so a running number is reproducible.
        parts_of_name.sort(key=lambda c: (min(m["centre"][1] for m in c), min(m["centre"][0] for m in c)))
        suffixes: list[str] = []
        for cluster in parts_of_name:
            if len(parts_of_name) == 1:
                suffixes.append("")
                continue
            streets = Counter(m["street"] for m in cluster if m["street"])
            street = min(streets, key=lambda n: (-streets[n], n)) if streets else ""
            suffixes.append(name_slug(street) or "place")
        repeats = Counter(suffixes)
        running: Counter = Counter()
        for cluster, suffix in zip(parts_of_name, suffixes):
            if repeats[suffix] > 1:
                running[suffix] += 1
                suffix = f"{suffix}-{running[suffix]}"
            spellings = Counter(m["name"] for m in cluster)
            name = min(spellings, key=lambda n: (-spellings[n], n))
            parts = [polygon for m in cluster for polygon in m["polys"]]
            wkt = geojson_to_wkt({"type": "MultiPolygon", "coordinates": parts})
            landmark_id = f"hamburg-{slug}-{key_slug}" + (f"-{suffix}" if suffix else "")
            records.append(
                {"landmark_id": landmark_id, "name": name, "geometry_raw": wkt, "category": category}
            )
    print(
        f"Hamburg {slug} groups: {len(groups)} name(s), {split} split by place "
        f"into {len(records)} landmark(s)",
        file=sys.stderr,
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
        generic = {"keine angabe", "wohnhaus"} | {
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
                groups.setdefault(key_slug, []).append(
                    {
                        "name": name,
                        "polys": parts,
                        "centre": centre(parts),
                        "street": street_of(props.get("info")),
                    }
                )
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
            ids = [r["landmark_id"] for r in grouped]
            if len(set(ids)) != len(ids) or seen.intersection(ids):
                raise RuntimeError("Hamburg heritage register: building group ids repeat")
            seen.update(ids)
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
