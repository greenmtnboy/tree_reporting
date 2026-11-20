#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Groningen's landmarks: its municipal and national monuments.

Two statutory designation registers, the runbook's first preference:

* **Gemeentelijke monumenten**, the monuments the city designates under its
  heritage bye-law: the WFS feature type `geo-data:Monumenten_gemeente_Groningen`
  on maps.groningen.nl/geoserver, the city's open-data record "Monumenten"
  (data.overheid.nl `groningen-monumenten-gemeente-groningen`), published by
  Gemeente Groningen under CC BY 4.0 ("CC-BY (4.0)",
  http://creativecommons.org/licenses/by/4.0/deed.nl).  5,697 polygons when
  this was written, keyed on `objectcode` (unique), of which only
  `beschermingscode` `G`, `Gemeentelijk monument` (681), is a designation
  read here.  `GL` ("monument light", 214, a facade-only protection, every
  one labelled by its address alone) and the city's lists of characteristic
  and townscape buildings (`Y`, `B`, `B2`, `N`: 4,802) are not monuments and
  are skipped.
* **Rijksmonumenten**, the national register kept by the Rijksdienst voor
  het Cultureel Erfgoed (RCE): the INSPIRE Protected Sites service on PDOK
  (`service.pdok.nl/rce/ps-ch/wfs/v1_0`, layers `rce_inspire_polygons` and
  `rce_inspire_points`), whose capabilities state CC BY 4.0
  (https://creativecommons.org/licenses/by/4.0/deed.nl).  Read as
  Eindhoven's are (nlein/eindhoven_landmarks.py): the number is the last
  segment of `ciCitation`, a monument's parts are grouped by it, and features
  in the box are kept when they fall inside the municipality's boundary (PDOK
  `bestuurlijkegebieden` `Gemeentegebied` GM0014, Kadaster).  Protected
  townscapes are areas, not monuments, and are skipped.

Published as `groningen-gm-{objectcode}` and `groningen-rm-{RCE number}`.

**Named only where the register gives a name.**  The municipal register's
`mon_objectnaam` is a label of address and description in one string
(`Grote Leliestraat 34, Pieternella Gasthuis`, `Hereweg 68, herenhuis`,
`Boerderij Middelberterweg 6`, `Herebrug`).  A label with no house number
is a name as it stands; otherwise its comma-separated parts without a digit
that begin with a capital and are not a bare building type are the name
(`Pieternella Gasthuis`), and the parts with a number are the address.  A
monument known only by an address and a building type is dropped with a
count.  RCE's `text` is the monument's name where it has one (most are
empty); empty, `-` and bare building types are dropped likewise.  A name
repeated on several monuments gets its address (municipal) or number
(national) appended.

Attributes ride along in existing `landmark_common` properties: `category`
(`Gemeentelijk monument` / `Rijksmonument`), `register_number` and
`address` (municipal only).
"""

import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit, get_json_with_retry
from shared.platforms.wfs import WfsLayer, geojson_to_wkt, iter_wfs_features

CITY_LAYER = WfsLayer(
    "https://maps.groningen.nl/geoserver/geo-data/ows",
    "geo-data:Monumenten_gemeente_Groningen",
    timeout=180,
)
CITY_PROPERTIES = ["objectcode", "mon_objectnaam", "beschermingscode", "geom"]
MUNICIPAL_CODE = "G"
RCE_WFS = "https://service.pdok.nl/rce/ps-ch/wfs/v1_0"
RCE_LAYERS = ("ps-ch:rce_inspire_polygons", "ps-ch:rce_inspire_points")
RCE_NAMESPACE = "nlps-rijksmonumenten"
BOUNDARY_WFS = "https://service.pdok.nl/kadaster/bestuurlijkegebieden/wfs/v1_0"
MUNICIPALITY_CODE = "GM0014"
# lat_min, lon_min, lat_max, lon_max: the municipality's envelope
# (53.106-53.313 N, 6.463-6.773 E), padded.
BBOX = (53.09, 6.44, 53.33, 6.79)
PAGE = 10000

MUNICIPAL_CATEGORY = "Gemeentelijk monument"
NATIONAL_CATEGORY = "Rijksmonument"
# A label part that only says what kind of building it is.
GENERIC_NAMES = frozenset(
    {"-", "kantoor", "woonhuis", "woning", "woningen", "boerderij", "pand", "villa",
     "schuur", "huis", "winkel", "fabriek", "school", "kerk", "kapel", "toren",
     "poort", "hek", "complex", "monument", "bedrijfspand", "herenhuis",
     "gemeenschapshuis", "pakhuis", "woningbouw", "woningcomplex", "urinoir",
     "winkelpand", "winkelhuis", "winkelwoonhuis", "gemeentehuis", "groningen",
     "haren", "ten boer"}
)
_DIGIT = re.compile(r"\d")


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split()).strip(" ;,")
    # `Nieuwe Kijk in "t Jatstraat`: the article 't with a double quote.
    text = text.replace(' "t ', " 't ")
    return text or None


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
    if text.startswith("t "):
        return "'" + text
    if text.startswith("'t "):
        return text
    return text[0].upper() + text[1:]


def split_label(label: str | None) -> tuple[str | None, str | None]:
    """`(name, address)` from a municipal `mon_objectnaam`."""
    text = clean(label)
    if text is None:
        return None, None
    if not _DIGIT.search(text):
        return usable_name(text), None
    parts = [p.strip() for p in text.split(",") if p.strip()]
    named = [
        p for p in parts
        if not _DIGIT.search(p) and (p[0].isupper() or p[0] in "\"'") and usable_name(p)
    ]
    address = ", ".join(p for p in parts if _DIGIT.search(p)) or None
    return (", ".join(named) if named else None), address


# --- Municipal register -----------------------------------------------------

def read_municipal(counts: Counter) -> list[dict]:
    records = []
    for page in iter_wfs_features(CITY_LAYER, sort_by="objectcode", properties=CITY_PROPERTIES):
        for feature in page:
            props = feature.get("properties") or {}
            counts["municipal: rows"] += 1
            if clean(props.get("beschermingscode")) != MUNICIPAL_CODE:
                continue
            counts["municipal: monuments"] += 1
            number = clean(props.get("objectcode"))
            polygons = polygons_of(feature.get("geometry"))
            if number is None or not polygons:
                counts["municipal: no number or polygon"] += 1
                continue
            name, address = split_label(props.get("mon_objectnaam"))
            if name is None:
                counts["municipal: named only by address"] += 1
                continue
            records.append(
                {
                    "landmark_id": f"groningen-gm-{number}",
                    "name": name,
                    "geometry_raw": geojson_to_wkt(shape_of(polygons)),
                    "category": MUNICIPAL_CATEGORY,
                    "register_number": number,
                    "address": address,
                }
            )
    if not counts["municipal: monuments"]:
        raise RuntimeError("Groningen monumenten: no `G` (gemeentelijk monument) rows; the layer changed")
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
                "landmark_id": f"groningen-rm-{number}",
                "name": name,
                "geometry_raw": geojson_to_wkt(shape),
                "category": NATIONAL_CATEGORY,
                "register_number": number,
                "address": None,
            }
        )
    counts["national: monuments in Groningen"] += len(numbers)
    counts["national: kept"] += len(records)
    return records


def transform(records: list[dict]) -> pa.Table:
    def col(name: str, typ: pa.DataType) -> pa.Array:
        return pa.array([r[name] for r in records], type=typ)

    return pa.table(
        {
            "landmark_id": col("landmark_id", pa.string()),
            "city": pa.array(["NLGRQ"] * len(records), type=pa.string()),
            "name": col("name", pa.string()),
            "geometry_raw": col("geometry_raw", pa.string()),
            "category": col("category", pa.string()),
            "register_number": col("register_number", pa.string()),
            "address": col("address", pa.string()),
        }
    )


if __name__ == "__main__":
    counts: Counter = Counter()
    records = read_municipal(counts) + read_national(counts)
    if not records:
        raise RuntimeError("Groningen monuments: no named monument in either register")
    repeats = Counter(r["name"] for r in records)
    by_address = Counter((r["name"], r["address"]) for r in records)
    for r in records:
        if repeats[r["name"]] > 1:
            suffix = (
                r["address"]
                if r["address"] and by_address[(r["name"], r["address"])] == 1
                else r["register_number"]
            )
            r["name"] = f"{r['name']}, {suffix}"
    print("Groningen monuments: " + ", ".join(f"{k} {v}" for k, v in counts.items()), file=sys.stderr)
    emit(transform(records))
