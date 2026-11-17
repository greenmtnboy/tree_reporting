#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Cologne's landmarks: the city's list of protected monuments (Denkmalliste).

Source: the Denkmalliste WFS on `geoportal.stadt-koeln.de` (MapServer), the
same geoportal as the trees, catalogued on offenedaten-koeln.de as "Liste der
Kölner Denkmäler" (dataset 1d8e7e99-42f3-4650-ac7d-a70012d3cff0) under
Datenlizenz Deutschland – Zero – Version 2.0.  It is the city's statutory
register of monuments under the North Rhine-Westphalia monument protection
act (DSchG NRW), kept by the Stadtkonservator, one feature per protected
object with its list number (`DE_05315000_A_5689`), designation date, address
and building date: the runbook's first preference, on the trees' own portal.

The register is split into layers by kind of object.  Five are read:

* `koeln_denkmal_gebaeude` -- buildings, ~8,000 polygons;
* `koeln_denkmal_gruenflaeche` -- protected gardens and green spaces;
* `koeln_denkmal_gewaesser` -- protected water features and the parks
  around them (Flora, Rheinpark, the Äußerer Grüngürtel);
* `koeln_denkmal_freiflaechen` -- protected squares and open spaces;
* `koeln_denkmal_punkte` -- small monuments (statues, wayside crosses,
  milestones), points.

Not read: `koeln_denkmal_alleen` and `koeln_denkmal_baum`, the protected
avenues and trees themselves, which are trees and not places near one (a
notable-register candidate rather than a landmark); `koeln_denkmal_linie`
(façades and walls, lines, and the server errors on it as GeoJSON); and
`koeln_denkmal_pufferzonen`, the one UNESCO buffer zone around the cathedral,
which covers the city centre.

**Named by `Bezeichnung`, and only where that is a name.**  Most of the
register describes an object by its type -- 4,869 `Wohnhaus`, 1,880 `Wohn-
u. Geschäftshaus`, `Schule`, `Wegekreuz`, `Villa` -- which says nothing to
someone asking what is near a tree.  A name made only of generic type words
(`GENERIC_WORDS`, including qualifiers such as `ehem.` and `(nur Fassade)`)
is dropped with a count; anything else in it keeps it (`Schule
(Humboldt-Gymnasium)`, `Hofanlage Kampshof`, `Siedlung "Märchensiedlung"`),
Zürich's rule for its inventory.  Family graves (`Grabstätte Becker`, 100-odd
on the village churchyards) are dropped too.  A housing estate is one
landmark per protected building, as the register has it.

**Keyed on the list number** (`cgn-A_5689`), one landmark per protected
monument.  The same number sits in several layers when a monument spans them
(the Flora botanical garden is in four: its grounds, its water, its buildings
and a point), and within the small-monument points a monument of several
pieces is several points of one number.  The first feature is kept, taking
the layers in `LAYERS` order (grounds before buildings before points) and
features within a layer by `gid`; the rest are counted.  `gid` itself is the
server's row id and is not used as the key.

**Buildings sharing a name are one landmark** (as Hamburg's are): a housing
estate the register protects house by house (`Siedlung "Märchensiedlung"`,
126 list numbers) is one place, keyed `cgn-building-{slug}` on a normalised
slug of the name, drawn as a MULTIPOLYGON of its members, carrying the
smallest list number (`collapse_named_groups`).  A building name held by one
list number keeps its `cgn-{list number}` id; the other categories are not
grouped.

Points and polygons arrive in EPSG:25832 (the server ignores `srsName`) and
are converted vertex by vertex with the tree ingest's `utm32_to_wgs84`.

The register's attributes ride along in existing `landmark_common`
properties: `category` (the layer, in English), `address`, `register_number`
(the list number), `designation_date` (`geschützt_seit`) and `year_built`
(`Baujahr`, when it is a single year).
"""

import re
import sys
import unicodedata
from collections import Counter
from datetime import date
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit
from shared.platforms.wfs import iter_wfs_features

sys.path.insert(0, str(Path(__file__).parent))
from cologne_tree_info import (  # noqa: E402
    SOURCE_SRS,
    GeoJsonWfsLayer,
    utm32_to_wgs84,
)

BASE_URL = "https://geoportal.stadt-koeln.de/wss/service/denkmalliste_wfs/guest"

BUILDING_CATEGORY = "Listed building"

# (layer, category), in the order a monument's geometry is taken from: a
# protected site that spans several layers (the Flora is a garden, its water,
# its buildings and a point) is one landmark, drawn by its whole grounds
# rather than by the footprint of one building in it.
LAYERS = (
    ("koeln_denkmal_gruenflaeche", "Listed garden or green space"),
    ("koeln_denkmal_gewaesser", "Listed water feature"),
    ("koeln_denkmal_freiflaechen", "Listed open space"),
    ("koeln_denkmal_gebaeude", BUILDING_CATEGORY),
    ("koeln_denkmal_punkte", "Listed small monument"),
)

ID_PREFIX = "cgn-"
NUMBER_PREFIX = "DE_05315000_"

# Words that name a kind of object, not an object.  A `Bezeichnung` made of
# nothing else is a type, not a name.  Compared after lower-casing and
# stripping the punctuation around a word (`Wohn-`, `u.`, `(ehem.`).
GENERIC_WORDS = frozenset(
    """
    wohnhaus wohnhäuser wohngebäude wohnbauten wohnblock wohnhausgruppe wohnheim
    altenwohnheim wohn u und mit der des eines einer teil teile rest reste ehem
    nur fassade fassaden hinterhaus doppelhaus geschäftshaus geschäftshauses
    gechäftshaus geschäfts büro bürogebäude verwaltungsgebäude verwaltungsbeg
    verwaltungsbegüde verwaltungsbau verwaltungs betriebsgebäude
    wirtschaftsgebäude gewerbebau werkstatthaus atelierhaus hotelgebäude
    lagergebäude fabrik fabrikgebäude fabrikhalle fabrikhallen industriebauten
    schule schulgebäude lehrerwohnung kindergarten villa halbvilla doppelvilla
    hofanlage kleingehöft gehöft herrenhaus friedhof platzanlage platzgestaltung
    grünanlage grünanlagen parkanlage gartenanlage vorgartenanlage
    kleingartenanlage kleingärten garten spielplatz teich allee sportanlage
    einfriedung wegekreuz bildstock kreuz dorfkreuz missionskreuz gedenkkreuz
    pfarrhaus pfarrgebäude pfarr pfarrzentrum gemeindehaus gemeinde
    gemeindezentrum kath ev kirche kapelle wegekapelle kloster heiligenhäuschen
    hausmadonna madonna fußfallstation brunnen pumpe wasserpumpe steinerne
    meilenstein grenzstein gedenkstein kriegerdenkmal kleindenkmal denkmal
    techn tech technisches td portal keller gewölbekeller kiosk bunker
    hochbunker tankstelle transformatorenhäuschen transformatorenhaus
    transformatorenstation umspannwerk stellwerk lokschuppen wasserturm
    wasserwerk pumpenhaus hallenbad eisenbahnbrücke bahnhof bahnhofsgebäude
    postamt institutsgebäude gerichtsgebäude krankenhaus museum hotel siedlung
    dorfschmiede ladenlokal ladenbau stallgebäude garagenanlage waldartige
    industriegebäude turnhalle kleingehöfts wirtschafts saalgebäude
    """.split()
)
_STRIP = ".,;:-()/\"'"
_GRAVE = re.compile(r"^(Grabstätte|Grabmal)\b")

_MONTHS = {
    "januar": 1, "jänner": 1, "februar": 2, "märz": 3, "april": 4, "mai": 5,
    "juni": 6, "juli": 7, "august": 8, "september": 9, "oktober": 10,
    "november": 11, "dezember": 12,
}
_GERMAN_DATE = re.compile(r"^\s*(\d{1,2})\.\s*([A-Za-zÄÖÜäöü]+)\s+(\d{4})\s*$")
_PLAIN_YEAR = re.compile(r"^\s*(\d{4})\s*$")


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def is_generic(name: str) -> bool:
    """True when every word of *name* is a generic type word (or a number)."""
    words = [w.strip(_STRIP).lower() for w in re.split(r"[\s/,]+", name)]
    words = [w for w in words if w]
    return bool(words) and all(w in GENERIC_WORDS or w.isdigit() for w in words)


def parse_german_date(value) -> date | None:
    """`18. September 1990` -> date(1990, 9, 18), or None."""
    text = clean(value)
    if text is None:
        return None
    m = _GERMAN_DATE.match(text)
    if not m:
        return None
    month = _MONTHS.get(m.group(2).lower())
    if month is None:
        return None
    try:
        return date(int(m.group(3)), month, int(m.group(1)))
    except ValueError:
        return None


def parse_year(value) -> int | None:
    text = clean(value)
    if text is None:
        return None
    m = _PLAIN_YEAR.match(text)
    return int(m.group(1)) if m else None


def _lonlat(pt: list) -> str:
    lat, lon = utm32_to_wgs84(float(pt[0]), float(pt[1]))
    return f"{lon:.7f} {lat:.7f}"


def _ring(ring: list) -> str:
    return "(" + ", ".join(_lonlat(pt) for pt in ring) + ")"


def geometry_to_wkt(geometry: dict | None) -> str | None:
    """A UTM 32N GeoJSON geometry as WGS84 WKT, or None."""
    if not geometry:
        return None
    kind = geometry.get("type")
    coords = geometry.get("coordinates")
    if not coords:
        return None
    if kind == "Point":
        return f"POINT({_lonlat(coords)})"
    if kind == "MultiPoint":
        return f"POINT({_lonlat(coords[0])})"
    if kind == "Polygon":
        return "POLYGON(" + ", ".join(_ring(r) for r in coords) + ")"
    if kind == "MultiPolygon":
        return "MULTIPOLYGON(" + ", ".join(
            "(" + ", ".join(_ring(r) for r in polygon) + ")" for polygon in coords
        ) + ")"
    return None


def _gid(feature: dict) -> int:
    try:
        return int((feature.get("properties") or {}).get("gid") or 0)
    except ValueError:
        return 0


def read_layer(type_name: str, category: str, counts: Counter) -> list[dict]:
    """Every usable feature of one layer, in `gid` order, as records."""
    layer = GeoJsonWfsLayer(BASE_URL, f"ms:{type_name}", timeout=300)
    features = [
        f for page in iter_wfs_features(layer, sort_by="gid", srs=SOURCE_SRS) for f in page
    ]
    if not features:
        raise RuntimeError(f"Cologne landmarks: {type_name} returned no features")
    features.sort(key=_gid)
    records: list[dict] = []
    for feature in features:
        props = feature.get("properties") or {}
        number = clean(props.get("Denkmalnummer"))
        name = clean(props.get("Bezeichnung"))
        if number is None or name is None:
            counts["no number or name"] += 1
            continue
        if _GRAVE.match(name):
            counts["family grave"] += 1
            continue
        if is_generic(name):
            counts["type, not a name"] += 1
            continue
        wkt = geometry_to_wkt(feature.get("geometry"))
        if wkt is None:
            counts["no geometry"] += 1
            continue
        records.append(
            {
                "landmark_id": f"{ID_PREFIX}{number.removeprefix(NUMBER_PREFIX)}",
                "name": name,
                "geometry_raw": wkt,
                "category": category,
                "address": clean(props.get("Adresse")),
                "register_number": number,
                "designation_date": parse_german_date(props.get("geschützt_seit")),
                "year_built": parse_year(props.get("Baujahr")),
                # Not published: what `collapse_named_groups` groups within.
                "district": clean(props.get("Stadtteil")),
            }
        )
    return records


_UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})


def name_slug(name: str) -> str:
    """`Siedlung "Märchensiedlung"` -> `siedlung-maerchensiedlung`."""
    text = name.casefold().translate(_UMLAUTS)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return "-".join(re.findall(r"[a-z0-9]+", text))


def _number_order(number: str) -> tuple:
    digits = re.findall(r"\d+", number.removeprefix(NUMBER_PREFIX))
    return (int(digits[-1]) if digits else 0, number)


def _wkt_body(wkt: str) -> tuple[str, list[str]]:
    """("polygon" | "point", the member parts) of one record's WKT."""
    kind, _, rest = wkt.partition("(")
    inner = rest[:-1]
    if kind == "POLYGON":
        return "polygon", [f"({inner})"]
    if kind == "MULTIPOLYGON":
        return "polygon", [inner]
    if kind == "POINT":
        return "point", [f"({inner})"]
    raise RuntimeError(f"Cologne landmarks: cannot merge geometry {kind!r}")


def merge_geometries(wkts: list[str]) -> str:
    """One MULTIPOLYGON (or MULTIPOINT) of every member's parts."""
    kinds: set[str] = set()
    parts: list[str] = []
    for wkt in wkts:
        kind, body = _wkt_body(wkt)
        kinds.add(kind)
        parts.extend(body)
    if kinds == {"polygon"}:
        return "MULTIPOLYGON(" + ", ".join(parts) + ")"
    if kinds == {"point"}:
        return "MULTIPOINT(" + ", ".join(parts) + ")"
    return "GEOMETRYCOLLECTION(" + ", ".join(wkts) + ")"


def collapse_named_groups(records: list[dict], category: str, counts: Counter) -> list[dict]:
    """One landmark per name and district among *category*'s records.

    A name the register gives to several list numbers in one category -- a
    housing estate protected house by house (`Siedlung "Märchensiedlung"`, 126
    numbers) -- is one place to someone asking what is near a tree, and one
    pin on the map: id `cgn-building-{slug}`, the members' polygons as one
    MULTIPOLYGON, the smallest list number, the earliest designation, the
    building year only when every member agrees, and no single address.  A
    name held by one record keeps its record, and its list-number id.

    **Within one Stadtteil.**  A name alone is not a place: `Kirche St.
    Joseph` is seven churches up to 11 km apart, `Feuerwache` five fire
    stations, `Hofanlage Bergerhof` five farms over 20 km, and one landmark
    drawn around them would sit in none of them.  A name grouped in more than
    one district takes the district's slug too
    (`cgn-building-{slug}-{district}`).
    """
    def key(record: dict) -> tuple[str, str]:
        return name_slug(record["name"]), name_slug(record.get("district") or "")

    groups: dict[tuple[str, str], list[dict]] = {}
    for record in records:
        if record["category"] == category:
            groups.setdefault(key(record), []).append(record)
    # Names merged in more than one district need the district in the id.
    merged_districts = Counter(k[0] for k, members in groups.items() if len(members) > 1)
    merged_ids: set[int] = set()
    out: list[dict] = []
    for record in records:
        if record["category"] != category:
            out.append(record)
            continue
        slug, district = key(record)
        members = groups[(slug, district)]
        if len(members) == 1:
            out.append(record)
            continue
        if id(members[0]) in merged_ids:
            continue
        merged_ids.add(id(members[0]))
        counts["merged into a same-named landmark"] += len(members) - 1
        dates = [m["designation_date"] for m in members if m["designation_date"]]
        years = {m["year_built"] for m in members}
        out.append(
            {
                "landmark_id": f"{ID_PREFIX}building-{slug}"
                + (f"-{district}" if merged_districts[slug] > 1 else ""),
                "name": members[0]["name"],
                "geometry_raw": merge_geometries([m["geometry_raw"] for m in members]),
                "category": category,
                "address": None,
                "register_number": min(
                    (m["register_number"] for m in members), key=_number_order
                ),
                "designation_date": min(dates) if dates else None,
                "year_built": years.pop() if len(years) == 1 else None,
            }
        )
    return out


def read_landmarks() -> list[dict]:
    """One record per list number, from the first layer (in `LAYERS` order) holding it."""
    counts: Counter = Counter()
    by_number: dict[str, dict] = {}
    for type_name, category in LAYERS:
        for record in read_layer(type_name, category, counts):
            if record["landmark_id"] in by_number:
                counts["same monument in an earlier layer"] += 1
                continue
            by_number[record["landmark_id"]] = record
    records = collapse_named_groups(list(by_number.values()), BUILDING_CATEGORY, counts)
    kept = Counter(r["category"] for r in records)
    print(
        f"Cologne landmarks: {len(records)} kept ({dict(kept)}); dropped {dict(counts)}",
        file=sys.stderr,
    )
    return records


def transform(records: list[dict]) -> pa.Table:
    def col(name: str, type_: pa.DataType) -> pa.Array:
        return pa.array([r[name] for r in records], type=type_)

    return pa.table(
        {
            "landmark_id": col("landmark_id", pa.string()),
            "city": pa.array(["DECGN"] * len(records), type=pa.string()),
            "name": col("name", pa.string()),
            "geometry_raw": col("geometry_raw", pa.string()),
            "category": col("category", pa.string()),
            "address": col("address", pa.string()),
            "register_number": col("register_number", pa.string()),
            "designation_date": col("designation_date", pa.date32()),
            "year_built": col("year_built", pa.int64()),
        }
    )


if __name__ == "__main__":
    emit(transform(read_landmarks()))
