#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Utrecht's landmarks: its national and municipal monuments, from the city's WFS.

Source: two layers on the Gemeente Utrecht GeoServer
(`geodata.utrecht.nl/geoserver/UtrechtOpen/wfs`), catalogued on
open.utrecht.nl as "Rijksmonumenten"
(https://open.utrecht.nl/dataset/rijksmonumenten/e30f329c-a1d9-45d8-a68b-91fbdf723703)
and "Gemeentelijke monumenten"
(https://open.utrecht.nl/dataset/gemeentelijke-monumenten/541670dd-315c-42e3-8b32-28735c13689d).
Both records state CC0 1.0 for the dataset and CC BY-SA 4.0 on each of its
distributions, the WFS among them; attribute as CC BY-SA 4.0.  These are the
city's copies of the two statutory designation registers -- the national
monuments (`RIJKSMONUMENTEN`, 1,567 polygons when this was written) and the
monuments the city itself designates under its heritage bye-law
(`GEMEENTELIJKE_MONUMENTEN`, 1,725) -- the runbook's first preference.

**Keyed on `MONUMENTNUMMER`**, the register number (the national register's
`monumentnummer` for a rijksmonument, the city's for a gemeentelijk
monument).  A monument drawn as several polygons repeats its number on each
row (the layers' own `ID` repeats with it), so rows are grouped by number
into one MULTIPOLYGON.  Published as `utrecht-rm-{n}` and `utrecht-gm-{n}`,
so the two number spaces cannot collide.

**Named only where the register gives a name.**  The registers have no name
column: `ADRNAAM` is the address (`OUDEGRACHT  0014`), followed for some
objects by `; ` and what the object is (`JANSKERKHOF  0026; JANSKERK`,
`DOMPLEIN; MONUMENT JAN VAN NASSAU`), and a bridge, a lock or a park is
written by its name alone (`MAGDALENABRUG`, `WEERDSLUIS`).  As in
Copenhagen and Hamburg, an address is a designation rather than a landmark
anyone asks for by name, so:

* `address; Label` publishes `Label` (`Janskerk`, `Kantoorgebouw
  Jongerius`); a label that is only a building type (`COMPLEX`, `VILLA`,
  `POORT`) or more house numbers is dropped with the address;
* `Place; Label` with no house number publishes `Label, Place`
  (`Verzetsmonument, Domplein`), or the place alone when the label only
  repeats it (`PARK OOG IN AL; PARK`);
* a value with no house number at all publishes as written (`Magdalenabrug`,
  `NHW-Fort Blauwkapel`), except a street marked `ONGENUMMERD`
  ("unnumbered");
* everything else -- the street address of a canal house -- is dropped,
  with a count.  That is most of both registers (about nine in ten rows).

The register is upper case; names are re-cased with Dutch particles in lower
case (`Monument voor de Binnenlandse Strijdkrachten`).

The register's attributes ride along in existing `landmark_common`
properties: `category` (`Rijksmonument` or `Gemeentelijk monument`),
`address` (the address part of `ADRNAAM`, when it has a house number) and
`year_built` (`BOUWJR_VAN`, when set).
"""

import re
import sys
from collections import Counter
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit
from shared.platforms.wfs import WfsLayer, geojson_to_wkt, iter_wfs_features

BASE_URL = "https://geodata.utrecht.nl/geoserver/UtrechtOpen/wfs"
REGISTERS = (
    # (layer, id prefix, category)
    (WfsLayer(BASE_URL, "UtrechtOpen:RIJKSMONUMENTEN"), "utrecht-rm-", "Rijksmonument"),
    (WfsLayer(BASE_URL, "UtrechtOpen:GEMEENTELIJKE_MONUMENTEN"), "utrecht-gm-", "Gemeentelijk monument"),
)
PROPERTIES = ["PK_ID", "MONUMENTNUMMER", "ADRNAAM", "BOUWJR_VAN", "GEOMETRIE"]

HOUSE_NUMBER = re.compile(r"\d")
UNNUMBERED = re.compile(r"\bONGEN", re.IGNORECASE)
# A label that says only what kind of building it is.
GENERIC_LABELS = frozenset(
    {"complex", "villa", "poort", "poortje", "achterhuis", "woonhuis", "pand", "bunker",
     "kelders", "kelder", "huis", "huizen", "woningen", "boerderij", "schuur", "koetshuis",
     "tuinprieel", "hek", "toegangshek", "pomp", "bank", "park", "brug", "kerk", "tuin",
     "hekwerk", "hoofdgebouw", "dienstwoning", "moestuin", "parkaanleg", "poortgebw",
     "begraafplt", "achtergevel", "voorgevel", "bouwdeel", "aanbouw", "kapel", "toren"}
)
LOWER_WORDS = frozenset(
    {"de", "het", "een", "van", "der", "den", "en", "op", "aan", "in", "bij", "voor", "tussen",
     "met", "naar", "over", "onder", "uit", "t/m", "tm", "c.a.", "'t", "'s", "of", "ten", "ter", "te"}
)
ACRONYMS = frozenset({"NHW", "NS", "WO", "II", "III", "IV", "PTT", "KNMI", "VOC"})
_YEAR_MIN, _YEAR_MAX = 1000, 2100


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split()).strip(" ;,.+")
    return text or None


def _case_word(word: str, first: bool) -> str:
    if word.upper() in ACRONYMS:
        return word.upper()
    lower = word.lower()
    if not first and lower in LOWER_WORDS:
        return lower
    if lower in {"'t", "'s"}:
        return lower
    # `NHW-FORT` -> `NHW-Fort`, `(GEBOUW` -> `(Gebouw`.
    return "-".join(
        part if part.upper() in ACRONYMS else re.sub(r"[a-zà-ÿ]", lambda m: m.group(0).upper(), part.lower(), count=1)
        for part in word.split("-")
    )


# The register's text lost a few characters to a bad encoding (U+FFFD).
MOJIBAKE = {"BARTHOLOME\ufffdBRUG": "BARTHOLOME\u00cfBRUG"}
INITIALS = re.compile(r"(?:[A-Za-z]\.)+")


def recase(text: str) -> str:
    for broken, fixed in MOJIBAKE.items():
        text = text.replace(broken, fixed)
    text = text.replace("\ufffd", "")
    words = []
    for i, word in enumerate(text.split()):
        if INITIALS.fullmatch(word):  # `N.S.`, `Z.O.`
            words.append(word.upper())
            continue
        cased = _case_word(word, i == 0)
        # Dutch `IJ` is one letter: `Ijsselsteinlaan` -> `IJsselsteinlaan`.
        words.append(re.sub(r"^([('\"]?)Ij", r"\1IJ", cased))
    # House numbers are zero-padded in the register: `Oudegracht 0014`.
    return re.sub(r"\b0+(\d)", r"\1", " ".join(words))


def name_of(adrnaam: str | None) -> tuple[str | None, str | None]:
    """`(name, address)` for one `ADRNAAM`; name None when it is only an address."""
    text = clean(adrnaam)
    if text is None:
        return None, None
    # `place; ...; label`: the first part says where, the last what.
    parts = [part.strip(" ,.+") for part in text.split(";")]
    place, label = parts[0], (parts[-1] if len(parts) > 1 else "")
    address = recase(place) if HOUSE_NUMBER.search(place) else None
    if label:
        if HOUSE_NUMBER.search(label) or UNNUMBERED.search(label):
            return None, address
        if address is not None:
            if label.lower() in GENERIC_LABELS:
                return None, address
            return recase(label), address
        if UNNUMBERED.search(place):
            return (None, None) if label.lower() in GENERIC_LABELS else (recase(label), None)
        if label.lower() in place.lower():
            return recase(place), None
        return f"{recase(label)}, {recase(place)}", None
    if address is not None or UNNUMBERED.search(place):
        return None, address
    return recase(place), None


def read_register(layer: WfsLayer, prefix: str, category: str, counts: Counter) -> list[dict]:
    groups: dict[int, dict] = {}
    for page in iter_wfs_features(layer, sort_by="PK_ID", properties=PROPERTIES):
        for feature in page:
            props = feature.get("properties") or {}
            counts[f"{category}: rows"] += 1
            number = props.get("MONUMENTNUMMER")
            geometry = feature.get("geometry") or {}
            if number is None or geometry.get("type") not in ("Polygon", "MultiPolygon"):
                counts[f"{category}: no number or polygon"] += 1
                continue
            polygons = (
                [geometry["coordinates"]] if geometry["type"] == "Polygon" else list(geometry["coordinates"])
            )
            group = groups.get(int(number))
            if group is None:
                groups[int(number)] = {"props": props, "polygons": polygons}
            else:
                group["polygons"].extend(polygons)
    records = []
    for number, group in groups.items():
        props = group["props"]
        name, address = name_of(props.get("ADRNAAM"))
        if name is None:
            counts[f"{category}: named only by address"] += 1
            continue
        polygons = group["polygons"]
        shape = (
            {"type": "Polygon", "coordinates": polygons[0]}
            if len(polygons) == 1
            else {"type": "MultiPolygon", "coordinates": polygons}
        )
        built = props.get("BOUWJR_VAN")
        try:
            built = int(built) if built is not None else None
        except (TypeError, ValueError):
            built = None
        records.append(
            {
                "landmark_id": f"{prefix}{number}",
                "name": name,
                "geometry_raw": geojson_to_wkt(shape),
                "category": category,
                "address": address,
                "year_built": built if built and _YEAR_MIN <= built <= _YEAR_MAX else None,
            }
        )
        counts[f"{category}: kept"] += 1
    counts[f"{category}: monuments"] += len(groups)
    return records


def transform(records: list[dict]) -> pa.Table:
    def col(name: str, typ: pa.DataType) -> pa.Array:
        return pa.array([r[name] for r in records], type=typ)

    return pa.table(
        {
            "landmark_id": col("landmark_id", pa.string()),
            "city": pa.array(["NLUTC"] * len(records), type=pa.string()),
            "name": col("name", pa.string()),
            "geometry_raw": col("geometry_raw", pa.string()),
            "category": col("category", pa.string()),
            "address": col("address", pa.string()),
            "year_built": col("year_built", pa.int64()),
        }
    )


if __name__ == "__main__":
    counts: Counter = Counter()
    records = [r for layer, prefix, category in REGISTERS for r in read_register(layer, prefix, category, counts)]
    # A complex of several registered blocks repeats its name on each
    # (`Complex Rijnesteinhof` 14 times); append the address so each label
    # says which, Vienna's and Ghent's rule.
    repeats = Counter(r["name"] for r in records)
    for r in records:
        if repeats[r["name"]] > 1 and r["address"]:
            r["name"] = f"{r['name']}, {r['address']}"
    if not records:
        raise RuntimeError("Utrecht monuments: no named monument in either register")
    print("Utrecht monuments: " + ", ".join(f"{k} {v}" for k, v in counts.items()), file=sys.stderr)
    emit(transform(records))
