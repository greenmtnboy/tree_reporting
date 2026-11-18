#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Eindhoven's landmarks: its municipal and national monuments.

Two statutory designation registers, the runbook's first preference:

* **Gemeentelijke monumenten**, the monuments the city designates under its
  heritage bye-law: OpenDataSoft dataset `monumenten` on the city's portal
  (https://data.eindhoven.nl/explore/dataset/monumenten/), published by
  Gemeente Eindhoven, `license` "Publiek domein" (public domain).  1,612
  polygons when this was written: 1,310 rows of 563 `Gemeentelijk monument`
  numbers (`GM0080`), and 302 `Cultuurhistorisch pand` rows (`CP0342`),
  buildings the city merely lists as of cultural-historical interest, which
  are not a designation and are skipped.  A monument drawn as several
  polygons (a housing complex, one per house) repeats its number on each, so
  rows are grouped by number into one MULTIPOLYGON.
* **Rijksmonumenten**, the national register kept by the Rijksdienst voor
  het Cultureel Erfgoed (RCE), which the city's dataset description points
  to: the INSPIRE Protected Sites service on PDOK
  (`service.pdok.nl/rce/ps-ch/wfs/v1_0`, layers `rce_inspire_polygons` and
  `rce_inspire_points`), whose capabilities state CC BY 4.0
  (https://creativecommons.org/licenses/by/4.0/deed.nl).  The monument
  number is the last segment of `ciCitation`
  (`https://monumentenregister.cultureelerfgoed.nl/monumenten/14636`); a
  monument with parts has one point per part (`localid` `16237.00`,
  `16237.01`), grouped by number.  The polygon is used where the register
  draws one, the first point otherwise.  The service is national and has no
  municipality attribute, so features are read in the city's box and kept
  when they fall inside the municipality's boundary (PDOK
  `bestuurlijkegebieden` `Gemeentegebied` GM0772, Kadaster).  The register's
  `nlps-stadsendorpsgezichten` (protected townscapes) are areas, not
  monuments, and are skipped.

Published as `eindhoven-gm-{GM number}` and `eindhoven-rm-{RCE number}`.

**Named only where the register gives a name.**  The municipal register
has `naam` on 87 numbers (`Natlab`, `Stadsschouwburg/Parktheater`); a
monument without one takes its `complex` when that complex is a single
monument (`Waterleidingbedrijf`), with the register's complex code dropped
(`CW007 - Winkelensemble Demer`); the rest are known only by an address
and are dropped with a count.  RCE's `text` is the monument's name where
it has one (`Evoluon`, `H. Lambertuskerk`); empty, `-` and bare building
types (`Kantoor`, `Woonhuis`) are dropped likewise.  A name repeated on
several monuments gets its address (municipal) or number (national)
appended, so each label says which.

Attributes ride along in existing `landmark_common` properties: `category`
(`Gemeentelijk monument` / `Rijksmonument`), `register_number`, `address`
(municipal only) and `year_built` (municipal `bouwjaar`, when it holds a
year).
"""

import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit, get_json_with_retry
from shared.platforms.wfs import geojson_to_wkt

CITY_EXPORT_URL = (
    "https://data.eindhoven.nl/api/explore/v2.1/catalog/datasets/monumenten/exports/geojson"
)
RCE_WFS = "https://service.pdok.nl/rce/ps-ch/wfs/v1_0"
RCE_LAYERS = ("ps-ch:rce_inspire_polygons", "ps-ch:rce_inspire_points")
RCE_NAMESPACE = "nlps-rijksmonumenten"
BOUNDARY_WFS = "https://service.pdok.nl/kadaster/bestuurlijkegebieden/wfs/v1_0"
MUNICIPALITY_CODE = "GM0772"
# lat_min, lon_min, lat_max, lon_max: the municipality's envelope, padded.
BBOX = (51.39, 5.35, 51.51, 5.56)
PAGE = 10000

MUNICIPAL_CATEGORY = "Gemeentelijk monument"
NATIONAL_CATEGORY = "Rijksmonument"
_COMPLEX_CODE = re.compile(r"^[A-Z]{1,3}\d{2,4}\s*-\s*")
_YEAR = re.compile(r"\b(1[0-9]{3}|20[0-9]{2})\b")
# A label that only says what kind of building it is.
GENERIC_NAMES = frozenset(
    {"-", "kantoor", "woonhuis", "woning", "woningen", "boerderij", "pand", "villa",
     "schuur", "huis", "winkel", "fabriek", "school", "kerk", "kapel", "toren",
     "poort", "hek", "complex", "monument", "bedrijfspand", "herenhuis",
     "gemeenschapshuis"}
)


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split()).strip(" ;,")
    return text or None


def year_of(value) -> int | None:
    match = _YEAR.search(clean(value) or "")
    return int(match.group(1)) if match else None


def polygons_of(geometry: dict | None) -> list:
    if not geometry:
        return []
    if geometry.get("type") == "Polygon":
        return [geometry["coordinates"]]
    if geometry.get("type") == "MultiPolygon":
        return list(geometry["coordinates"])
    return []


def shape_of(polygons: list) -> dict:
    if len(polygons) == 1:
        return {"type": "Polygon", "coordinates": polygons[0]}
    return {"type": "MultiPolygon", "coordinates": polygons}


def usable_name(text: str | None) -> str | None:
    text = clean(text)
    if text is None or text.lower() in GENERIC_NAMES:
        return None
    # `t Withuis`: the article 't with its apostrophe lost.
    if text.startswith("t "):
        return "'" + text
    if text.startswith("'t "):
        return text
    return text[0].upper() + text[1:]


# --- Municipal register -----------------------------------------------------

def read_municipal(counts: Counter) -> list[dict]:
    payload = get_json_with_retry(CITY_EXPORT_URL, timeout=180)
    features = payload.get("features")
    if not isinstance(features, list):
        raise RuntimeError("data.eindhoven.nl monumenten: the export has no features")
    groups: dict[str, list[dict]] = defaultdict(list)
    for feature in features:
        props = feature.get("properties") or {}
        counts["municipal: rows"] += 1
        if clean(props.get("categorie")) != MUNICIPAL_CATEGORY:
            counts["municipal: not a designation (Cultuurhistorisch pand)"] += 1
            continue
        number = clean(props.get("object_nummer"))
        if number is None or not polygons_of(feature.get("geometry")):
            counts["municipal: no number or polygon"] += 1
            continue
        groups[number].append(feature)

    complex_numbers: dict[str, set[str]] = defaultdict(set)
    for number, rows in groups.items():
        for row in rows:
            name = clean(row["properties"].get("complex"))
            if name:
                complex_numbers[_COMPLEX_CODE.sub("", name).lower()].add(number)

    records = []
    for number, rows in groups.items():
        props_list = [row["properties"] for row in rows]
        names = Counter(n for p in props_list if (n := usable_name(p.get("naam"))))
        name = names.most_common(1)[0][0] if names else None
        if name is None:
            complexes = {
                _COMPLEX_CODE.sub("", c) for p in props_list if (c := clean(p.get("complex")))
            }
            if len(complexes) == 1:
                candidate = complexes.pop()
                if len(complex_numbers[candidate.lower()]) == 1:
                    name = usable_name(candidate)
        if name is None:
            counts["municipal: named only by address"] += 1
            continue
        polygons = [poly for row in rows for poly in polygons_of(row.get("geometry"))]
        addresses = sorted({a for p in props_list if (a := clean(p.get("adres")))})
        years = [y for p in props_list if (y := year_of(p.get("bouwjaar")))]
        records.append(
            {
                "landmark_id": f"eindhoven-gm-{number}",
                "name": name,
                "geometry_raw": geojson_to_wkt(shape_of(polygons)),
                "category": MUNICIPAL_CATEGORY,
                "register_number": number,
                "address": addresses[0] if addresses else None,
                "year_built": min(years) if years else None,
            }
        )
    counts["municipal: monuments"] += len(groups)
    counts["municipal: kept"] += len(records)
    return records


# --- National register (RCE) ------------------------------------------------

def _in_ring(lon: float, lat: float, ring: list) -> bool:
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def in_polygons(lon: float, lat: float, polygons: list) -> bool:
    return any(
        _in_ring(lon, lat, poly[0]) and not any(_in_ring(lon, lat, hole) for hole in poly[1:])
        for poly in polygons
    )


def municipality_polygons() -> list:
    payload = get_json_with_retry(
        BOUNDARY_WFS,
        params={
            "service": "WFS",
            "version": "2.0.0",
            "request": "GetFeature",
            "typeNames": "bestuurlijkegebieden:Gemeentegebied",
            "outputFormat": "application/json",
            "srsName": "EPSG:4326",
            "filter": (
                "<Filter><PropertyIsEqualTo><ValueReference>identificatie</ValueReference>"
                f"<Literal>{MUNICIPALITY_CODE}</Literal></PropertyIsEqualTo></Filter>"
            ),
        },
        timeout=120,
    )
    polygons = [p for f in payload.get("features") or [] for p in polygons_of(f.get("geometry"))]
    if not polygons:
        raise RuntimeError(f"PDOK bestuurlijkegebieden: no boundary for {MUNICIPALITY_CODE}")
    return polygons


def rce_features(layer: str) -> list[dict]:
    lat0, lon0, lat1, lon1 = BBOX
    payload = get_json_with_retry(
        RCE_WFS,
        params={
            "service": "WFS",
            "version": "2.0.0",
            "request": "GetFeature",
            "typeNames": layer,
            "outputFormat": "application/json",
            "srsName": "EPSG:4326",
            "count": PAGE,
            "bbox": f"{lat0},{lon0},{lat1},{lon1},urn:ogc:def:crs:EPSG::4326",
        },
        timeout=180,
    )
    features = payload.get("features")
    if not isinstance(features, list):
        raise RuntimeError(f"PDOK RCE {layer}: no features array")
    if len(features) >= PAGE:
        raise RuntimeError(f"PDOK RCE {layer}: {len(features)} features fill a page; page the read")
    return features


def monument_number(props: dict) -> str | None:
    citation = clean(props.get("ciCitation")) or ""
    tail = citation.rstrip("/").rsplit("/", 1)[-1]
    return tail if tail.isdigit() else None


def representative_point(geometry: dict) -> tuple[float, float] | None:
    kind = geometry.get("type")
    coords = geometry.get("coordinates")
    if kind == "Point":
        return coords[0], coords[1]
    if kind == "MultiPoint":
        return coords[0][0], coords[0][1]
    ring = (polygons_of(geometry) or [[[]]])[0][0]
    if not ring:
        return None
    return sum(p[0] for p in ring) / len(ring), sum(p[1] for p in ring) / len(ring)


def read_national(counts: Counter) -> list[dict]:
    boundary = municipality_polygons()
    polygons: dict[str, list] = defaultdict(list)
    points: dict[str, dict] = {}
    names: dict[str, Counter] = defaultdict(Counter)
    for layer in RCE_LAYERS:
        for feature in rce_features(layer):
            props = feature.get("properties") or {}
            if props.get("namespace") != RCE_NAMESPACE:
                continue
            geometry = feature.get("geometry") or {}
            number = monument_number(props)
            where = representative_point(geometry) if geometry else None
            if number is None or where is None:
                counts["national: no number or geometry"] += 1
                continue
            if not in_polygons(where[0], where[1], boundary):
                continue
            if layer.endswith("polygons"):
                polygons[number].extend(polygons_of(geometry))
            else:
                points.setdefault(number, geometry)
            if name := usable_name(props.get("text")):
                names[number][name] += 1
    numbers = set(polygons) | set(points)
    records = []
    for number in sorted(numbers, key=int):
        name = names[number].most_common(1)[0][0] if names[number] else None
        if name is None:
            counts["national: unnamed"] += 1
            continue
        shape = shape_of(polygons[number]) if polygons[number] else points[number]
        records.append(
            {
                "landmark_id": f"eindhoven-rm-{number}",
                "name": name,
                "geometry_raw": geojson_to_wkt(shape),
                "category": NATIONAL_CATEGORY,
                "register_number": number,
                "address": None,
                "year_built": None,
            }
        )
    counts["national: monuments in Eindhoven"] += len(numbers)
    counts["national: kept"] += len(records)
    return records


def transform(records: list[dict]) -> pa.Table:
    def col(name: str, typ: pa.DataType) -> pa.Array:
        return pa.array([r[name] for r in records], type=typ)

    return pa.table(
        {
            "landmark_id": col("landmark_id", pa.string()),
            "city": pa.array(["NLEIN"] * len(records), type=pa.string()),
            "name": col("name", pa.string()),
            "geometry_raw": col("geometry_raw", pa.string()),
            "category": col("category", pa.string()),
            "register_number": col("register_number", pa.string()),
            "address": col("address", pa.string()),
            "year_built": col("year_built", pa.int64()),
        }
    )


if __name__ == "__main__":
    counts: Counter = Counter()
    records = read_municipal(counts) + read_national(counts)
    if not records:
        raise RuntimeError("Eindhoven monuments: no named monument in either register")
    # A name on several monuments gets its address; where the address
    # repeats too (eight numbers of `Landgoed De Wielewaal` share one), or
    # there is none, its register number.
    repeats = Counter(r["name"] for r in records)
    by_address = Counter((r["name"], r["address"]) for r in records)
    for r in records:
        if repeats[r["name"]] > 1:
            suffix = r["address"] if r["address"] and by_address[(r["name"], r["address"])] == 1 else r["register_number"]
            r["name"] = f"{r['name']}, {suffix}"
    print("Eindhoven monuments: " + ", ".join(f"{k} {v}" for k, v in counts.items()), file=sys.stderr)
    emit(transform(records))
