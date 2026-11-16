#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Zürich's heritage inventory (Denkmalpflege-Inventar), from the city's WFS.

Source: `denkmalpflege_inventar_p` on `www.ogd.stadt-zuerich.ch`, the same
QGIS Server geoportal as the trees, catalogued on data.stadt-zuerich.ch as
`geo_denkmalpflege_inventar` under CC0.  It is the city monument office's
inventory of buildings and structures of art-historical and cultural
significance -- one point per inventoried building, **9,878** when this was
written -- each classed by the level at which it matters (`inventarka`:
`kommunal`, `regional`, `kantonal`) and flagged when formally placed under
protection (`unterschut` = `Ja`, 2,424 of them).  The runbook's first
preference: an official designation register, on the trees' own portal.

**Keyed on `id`, the inventory's object number**: 9,878 distinct values over
9,878 rows, none null.

**Named by `objektbeze`, and only where that is a name.**  The register
describes a building by what it is as often as by what it is called: 2,181
rows have no `objektbeze` at all, and several hundred more carry only a
building type (`Mehrfamilienhaus`, `Wohnhaus`, `Wohn- und Geschäftshaus`) or
an address (`Bändlistrasse 86/88`), which says nothing to someone asking what
is near a tree.  Those are dropped with a count; the rest -- `Grossmünster`,
`Kongresshaus und Tonhalle Zürich`, `Wohnsiedlung Neubühl` -- publish, one
point per building, so a housing estate of 121 inventoried houses is 121
landmarks of one name, as the register has it.

The register's own attributes ride along in existing `landmark_common`
properties: `category` is the inventory class (`Kommunal`, `Regional`,
`Kantonal`), `protection_type` is `Unter Schutz` for a formally protected
object and null otherwise, `address` and `year_built` (`baujahr`, when it is a
plain year).

**Regional and cantonal objects only.**  The communal class is 5,800 named
buildings, most of them ordinary houses and schools; on a tree map that is a
pin on every block.  The 650 regional and cantonal objects are the ones a
visitor would recognise.
"""

import re
import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit, get_json_with_retry, get_with_retry
from shared.platforms.wfs import geojson_to_wkt

SOURCE_URL = "https://www.ogd.stadt-zuerich.ch/wfs/geoportal/Denkmalpflege_Inventar"
TYPE_NAME = "denkmalpflege_inventar_p"
TIMEOUT = 180
# The inventory classes that publish (see the module docstring).
KEPT_LEVELS = frozenset({"regional", "kantonal"})

# A name made only of these words is a building type, not a name.
GENERIC_WORDS = frozenset(
    w.lower()
    for w in (
        "Wohnhaus Wohnhäuser Mehrfamilienhaus Mehrfamilienhäuser Einfamilienhaus "
        "Einfamilienhäuser Zweifamilienhaus Zweifamilienhäuser Doppelwohnhaus "
        "Doppelwohnhäuser Doppelmehrfamilienhaus Doppelmehrfamilienhäuser "
        "Doppel-Mehrfamilienhaus Doppeleinfamilienhaus Reihenhaus Reihenhäuser "
        "Reiheneinfamilienhaus Reiheneinfamilienhäuser Wohnhochhaus Wohnhochhäuser "
        "Wohnsiedlung Siedlung Geschäftshaus Geschäftshäuser Bürohaus Bürogebäude "
        "Wohn- Geschäfts- Büro- Gewerbe- Gewerbebau Gewerbehaus Bauernhaus "
        "Bauernhäuser Bauernhof Rebbauernhaus Rebbauernhäuser Flarzhaus Flarzhäuser "
        "Flarzreihe Villa Villen Doppelvilla Doppelvillen Lagerhaus Scheune Schopf "
        "Waschhaus Schulhaus Bootshaus Bootshäuser Brunnen Bruchsteinmauer Mauer "
        "Tramwartehalle Garage Garagen Remise Nebengebäude Ökonomiegebäude Wohnteil "
        "Stall Trotte Gartenhaus Pavillon Kiosk Wohnbauten Wohnbau Häuser Haus "
        "und mit sowie ehem. ehemaliges ehemalige"
    ).split()
)
# An address is street words and house numbers only: `Bändlistrasse 86/88`,
# `Rainstrasse 62-78`, `Lägern-/Rütschi-/Thurwiesenstrasse`,
# `Gertrudstrasse 54–72, 65`.
_STREET = re.compile(r"(strasse|str\.|gasse|weg|platz|quai|steig|gässli)$", re.IGNORECASE)
_HOUSE_NUMBER = re.compile(r"^\d+[a-z]?([\-–/]\d+[a-z]?)*$")
_YEAR = re.compile(r"^\d{3,4}$")


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def is_a_name(text: str) -> bool:
    """False for a building type (`Wohnhaus`) or an address standing in for a name."""
    words = [w.strip(",;:()") for w in text.split()]
    words = [w for w in words if w]
    if all(w.lower() in GENERIC_WORDS for w in words):
        return False
    if all(_STREET.search(w) or _HOUSE_NUMBER.match(w) or w.lower() == "und" for w in words):
        return False
    return True


def feature_count() -> int:
    response = get_with_retry(
        SOURCE_URL,
        params={"service": "WFS", "version": "1.1.0", "request": "GetFeature",
                "typename": TYPE_NAME, "resultType": "hits"},
        timeout=TIMEOUT,
    )
    match = re.search(r'numberOfFeatures="(\d+)"', response.text)
    if match is None:
        raise RuntimeError(f"{TYPE_NAME}: resultType=hits gave no numberOfFeatures")
    return int(match.group(1))


def read_inventory() -> list[dict]:
    expected = feature_count()
    payload = get_json_with_retry(
        SOURCE_URL,
        params={"service": "WFS", "version": "1.1.0", "request": "GetFeature",
                "typename": TYPE_NAME, "outputFormat": "GeoJSON", "srsName": "EPSG:4326"},
        timeout=TIMEOUT,
    )
    features = payload.get("features")
    if features is None:
        raise RuntimeError(f"{TYPE_NAME}: GetFeature answered without a features array")
    if len(features) != expected:
        raise RuntimeError(
            f"{TYPE_NAME}: read {len(features)} feature(s) but the layer reports {expected}"
        )
    records: list[dict] = []
    seen: set[str] = set()
    unusable = unnamed = communal = 0
    for feature in features:
        props = feature.get("properties") or {}
        key = clean(props.get("id"))
        wkt = geojson_to_wkt(feature.get("geometry"))
        if key is None or wkt is None or key in seen:
            unusable += 1
            continue
        seen.add(key)
        name = clean(props.get("objektbeze"))
        if name is None or not is_a_name(name):
            unnamed += 1
            continue
        level = clean(props.get("inventarka"))
        if level is None or level.lower() not in KEPT_LEVELS:
            communal += 1
            continue
        built = clean(props.get("baujahr"))
        records.append(
            {
                "landmark_id": f"zurich-dpi-{key}",
                "name": name,
                "geometry_raw": wkt,
                "category": level.capitalize() if level else None,
                "protection_type": "Unter Schutz" if clean(props.get("unterschut")) == "Ja" else None,
                "address": clean(props.get("adresse")),
                "year_built": int(built) if built and _YEAR.match(built) else None,
            }
        )
    print(
        f"Zürich Denkmalpflege-Inventar: {len(records)} kept of {expected}; dropped "
        f"{unusable} with no id or point and {unnamed} named only by building type, "
        f"address or not at all, and {communal} communal-class objects",
        file=sys.stderr,
    )
    return records


def transform(records: list[dict]) -> pa.Table:
    def col(name: str, typ: pa.DataType) -> pa.Array:
        return pa.array([r[name] for r in records], type=typ)

    return pa.table(
        {
            "landmark_id": col("landmark_id", pa.string()),
            "city": pa.array(["CHZUR"] * len(records), type=pa.string()),
            "name": col("name", pa.string()),
            "geometry_raw": col("geometry_raw", pa.string()),
            "category": col("category", pa.string()),
            "protection_type": col("protection_type", pa.string()),
            "address": col("address", pa.string()),
            "year_built": col("year_built", pa.int64()),
        }
    )


if __name__ == "__main__":
    emit(transform(read_inventory()))
