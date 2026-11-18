#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Leipzig's landmarks: the city's named parks and green spaces.

LICENCE: the publisher states no licence for this layer.  It is served on the
same open-data WFS as the tree register, but unlike the register it has no
opendata.leipzig.de record, and the service's own `AccessConstraints` and
`Fees` are empty.  It is reproduced for display only -- attributed to Stadt
Leipzig, not licensed for reuse.

Source: `OpenData:gruenflaechen_amt67` on
`geodienste.leipzig.de/l3/OpenData/wfs`, the green-space register of the Amt
für Stadtgrün und Gewässer (the office that keeps the tree register): 2,733
polygons when this was written, each a maintenance sub-area
(`nr_teilflaeche`, `401.009-2`) of a green space (`nr_gruenflaeche`, the same
code the tree register's `objektkuerzel` carries) with the sub-area's name.
Saxony's heritage register (Kulturdenkmalliste, Landesamt für Denkmalpflege)
is not published as open data -- only as a map viewer -- so the runbook's first
preference is not available; this is its second, a dataset on the trees' own
portal.

**A name is kept only where it is a place's name.**  The register names a
maintenance area by whatever locates it: a park (`Clara-Zetkin-Park`,
`Palmengarten`, `Friedenspark (Neuer Johannisfriedhof) östlich Hauptweg`), a
playground (`Spielplatz "Henriettenpark"`), but also a housing estate's
verge (`WG Grünau WK 4-Stuttgarter Allee (...)`), a planning mitigation
(`Kompensationsmaßnahme zum B-Plan 383`), a cadastral parcel (`Lausen
33/3`) or a street corner (`Hanns-Eisler-Straße/Bernhardstraße`).  So:

* the name is cut at its first parenthesis and at a directional tail
  (`nördlich`, `zwischen`, ...), so a park's sub-areas share one name;
* a name naming a planning instrument, an estate verge, a biotope number or
  an access strip (`DENY`) is dropped;
* what is left must contain a place noun (`PLACE_WORDS`: park, garten,
  friedhof, platz, anger, hain, see, teich, ...) -- a bare street corner or
  parcel number has none.

**Grouped by name and place**, Hamburg's rule: the polygons sharing a name
publish as one landmark (a MULTIPOLYGON), split into clusters at 1 km
single-linkage between polygon centres, so a park drawn as twenty sub-areas is
one landmark while two playgrounds both called `Kletterplatz` stay two.  The
id is a slug of the name (`leipzig-park-clara-zetkin-park`), with a running
number south to north when a name forms more than one cluster: the layer's
`objectid` and feature id are row numbers, and the sub-area codes are finer
than a landmark.  `category` is the register's `objektart` in English.
"""

import math
import re
import sys
from collections import Counter
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import CITY_BOUNDS, emit, get_json_with_retry  # noqa: E402

SOURCE_URL = "https://geodienste.leipzig.de/l3/OpenData/wfs"
TYPE_NAME = "OpenData:gruenflaechen_amt67"
TIMEOUT = 300
CLUSTER_KM = 1.0

CATEGORIES = {
    "Grün- und Parkanlagen": "Park",
    "Erholungsgärten": "Allotment garden",
    "Biotoppflegeflächen": "Nature area",
    "Wald": "Woodland",
}
PLAYGROUND = "Playground"

PLACE_WORDS = (
    "park", "garten", "gärten", "friedhof", "platz", "plan", "anger", "hain",
    "see", "teich", "wäldchen", "wald", "aue", "insel", "promenade", "hügel",
    "berg", "hafen", "bad", "gedenk", "denkmal", "ring", "wiese", "lache",
)
DENY = re.compile(
    r"b-plan|bebauungsplan|kompensation|ausgleich|^biotop|^wg\b|wohngebiet|"
    r"regenwasser|zufahrt|randstreifen|garagenhof|durchgang|abschnitt|"
    r"erweiterung|^\d|\b\d+/\d+\b",
    re.IGNORECASE,
)
_TAIL = re.compile(
    r"\s*(\(|\b(nördlich|südlich|östlich|westlich|zwischen|entlang|gegenüber)\b|"
    r"\s-\s*\d+\s*$|\d+\.\s*BA\b).*$",
    re.IGNORECASE,
)
_FOLD = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def place_name(raw) -> str | None:
    """The register's sub-area name reduced to a place's name, or None."""
    text = clean(raw)
    if text is None:
        return None
    if text.casefold().startswith("spielplatz"):
        # A playground's own name is in quotes; keep it whole.
        name = text
    else:
        name = clean(_TAIL.sub("", text))
    if name is None or DENY.search(name):
        return None
    folded = name.casefold()
    if not any(word in folded for word in PLACE_WORDS):
        return None
    return name.rstrip(" -,")


def name_slug(name: str) -> str:
    text = name.casefold().translate(_FOLD)
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def polygons(geometry: dict) -> list:
    kind = geometry.get("type")
    coords = geometry.get("coordinates") or []
    if kind == "Polygon":
        return [coords]
    if kind == "MultiPolygon":
        return list(coords)
    return []


def centre(polys: list) -> tuple[float, float]:
    """The mean vertex of the outer rings: a polygon's rough centre."""
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


def ring_wkt(ring: list) -> str:
    return ", ".join(f"{pt[0]} {pt[1]}" for pt in ring)


def multipolygon_wkt(polys: list) -> str:
    return "MULTIPOLYGON(" + ", ".join(
        "(" + ", ".join(f"({ring_wkt(r)})" for r in polygon) + ")" for polygon in polys
    ) + ")"


def read_features() -> list[dict]:
    payload = get_json_with_retry(
        SOURCE_URL,
        params={
            "SERVICE": "WFS",
            "VERSION": "2.0.0",
            "REQUEST": "GetFeature",
            "TYPENAMES": TYPE_NAME,
            "SRSNAME": "EPSG:4326",
            "outputFormat": "application/json",
        },
        timeout=TIMEOUT,
    )
    features = payload.get("features")
    if not features:
        raise RuntimeError(f"Leipzig green spaces: {TYPE_NAME} returned no features")
    total = payload.get("numberMatched")
    if isinstance(total, int) and total != len(features):
        raise RuntimeError(
            f"Leipzig green spaces: read {len(features)} of {total} features; "
            "the server capped the response"
        )
    return features


def category_of(name: str, objektart: str | None) -> str:
    if name.casefold().startswith("spielplatz"):
        return PLAYGROUND
    if objektart not in CATEGORIES:
        raise RuntimeError(f"Leipzig green spaces: unknown objektart {objektart!r}")
    return CATEGORIES[objektart]


def read_landmarks() -> list[dict]:
    south, north, west, east = CITY_BOUNDS["DELEJ"]
    groups: dict[str, list[dict]] = {}
    dropped = Counter()
    features = read_features()
    for feature in features:
        props = feature.get("properties") or {}
        parts = polygons(feature.get("geometry") or {})
        if not parts:
            dropped["no_polygon"] += 1
            continue
        name = place_name(props.get("name_teilflaeche"))
        if name is None:
            dropped["not_a_place_name"] += 1
            continue
        slug = name_slug(name)
        if not slug:
            dropped["not_a_place_name"] += 1
            continue
        lon, lat = centre(parts)
        if not (south <= lat <= north and west <= lon <= east):
            dropped["outside"] += 1
            continue
        groups.setdefault(slug, []).append(
            {
                "name": name,
                "polys": parts,
                "centre": (lon, lat),
                "category": category_of(name, clean(props.get("objektart"))),
            }
        )

    records: list[dict] = []
    for slug in sorted(groups):
        parts_of_name = clusters(groups[slug])
        parts_of_name.sort(
            key=lambda c: (min(m["centre"][1] for m in c), min(m["centre"][0] for m in c))
        )
        for n, cluster in enumerate(parts_of_name, start=1):
            names = Counter(m["name"] for m in cluster)
            categories = Counter(m["category"] for m in cluster)
            suffix = f"-{n}" if len(parts_of_name) > 1 else ""
            records.append(
                {
                    "landmark_id": f"leipzig-park-{slug}{suffix}",
                    "name": min(names, key=lambda k: (-names[k], k)),
                    "geometry_raw": multipolygon_wkt(
                        [poly for m in cluster for poly in m["polys"]]
                    ),
                    "category": min(categories, key=lambda k: (-categories[k], k)),
                }
            )
    ids = [r["landmark_id"] for r in records]
    if len(set(ids)) != len(ids):
        raise RuntimeError("Leipzig green spaces: landmark ids repeat")
    kept = sum(len(v) for v in groups.values())
    print(
        f"Leipzig green spaces: {len(features)} polygons, {kept} kept as "
        f"{len(records)} named landmark(s); dropped {dict(dropped)}",
        file=sys.stderr,
    )
    return records


def transform(records: list[dict]) -> pa.Table:
    return pa.table(
        {
            "landmark_id": pa.array([r["landmark_id"] for r in records], type=pa.string()),
            "city": pa.array(["DELEJ"] * len(records), type=pa.string()),
            "name": pa.array([r["name"] for r in records], type=pa.string()),
            "geometry_raw": pa.array([r["geometry_raw"] for r in records], type=pa.string()),
            "category": pa.array([r["category"] for r in records], type=pa.string()),
        }
    )


if __name__ == "__main__":
    emit(transform(read_landmarks()))
