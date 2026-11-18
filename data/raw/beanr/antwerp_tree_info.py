#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Antwerp's municipal tree inventory ("boom"), from Stad Antwerpen's geodata server.

Source: layer 676 `boom` of the city's public portal MapServer
(https://geodata.antwerpen.be/arcgissql/rest/services/P_Portal/portal_publiek6/MapServer/676),
the layer the city's open-data Hub item points to
(https://portaal-stadantwerpen.opendata.arcgis.com/datasets/stadAntwerpen::boom;
source table `sdedgeo.LAAG.GROENINVENTARIS_BOOM`, maintained by Stadsbeheer /
Groenbeheer).  **134,918 rows** when this was written.  Licence: the city's
"Gratis Open Data Licentie" (https://www.antwerpen.be/info/gratis-open-data-licentie;
the Hub catalogue says every dataset is under it), the Flemish Modellicentie
Gratis Hergebruik: reuse for any lawful purpose, commercial included, with the
attribution "bron stad Antwerpen".  The item's own `licenseInfo` ("Gebruiken
als referentie, niet als limitatieve lijst") is a completeness disclaimer.

The layer description: every tree in public space that the city manages,
woody plants over 3 m (some low pollards and trained forms are not counted);
woodland trees are not managed individually and are mapped as polygons
elsewhere, not here.  It covers the whole municipality -- all nine districts
plus Borsbeek (merged in 2025), and a few dozen city-managed trees just over
the line in Brasschaat, Edegem and Mortsel.  Layer 679 `toekomstboom`
("future trees", the trees the 2018 tree plan designates to grow old) is a
management designation, not an inventory, and is not read.

**Keyed on `OBJECT_GUID`**, the asset system's GUID for the tree, verified
non-null and unique over the whole layer (a `GROUP BY ... HAVING COUNT > 1`
statistics query returns nothing); published as `antwerp-{guid}` lower-cased.
`ANTW_ID` (the city's numeric asset id) is also unique; `OBJECTID` is the
publication's row number and is not used.  A row without one is dropped
with a count.

**Species is `LATBOOMSOORT`**, a scientific name with the cultivar quoted
(`Acer platanoides 'Schwedleri'`); `GENUS` is empty on every row.  10.8k rows
(8%) have no species and publish as `Unknown`.  The register's habits
handled here, the same family as Ghent's: a hybrid mark written after the
name, `Platanus hispanica (x)` (9.6k rows) and `Tilia europaea 'Euchlora' (x)`,
read as `Genus x epithet`; `(x C)` on `Cupressocyparis leylandii`, the
intergeneric mark, dropped (the synonym map folds the name); other notes in
parentheses (`(=Sophora japonica)`, `(bospest)`, `(Red Star)`) removed;
curly, backtick and mis-encoded quotes (`‘Erecta’`, `` `Summit¿ ``)
normalised to `'`; an apostrophe inside a cultivar (`'Faassen's Black'`,
`'Reine Claude d'Althan'`) passed as the cultivar column; a trade name in
capitals after the cultivar (`'Austep' FAIRVIEW FLAME`) dropped; and the
authority written as a cultivar (`Tilia tomentosa `Moench¿`) removed.  There
is no common-name column.

**Stem size** is `STAMOMTREK`, the trunk circumference in cm (median ~65,
mean 78; 12.4k rows null, 26 zero, which `enforce_tree_schema` nulls).
No planting date, height or crown spread is published, and no column marks
a felled tree or an empty planting site.
"""

import math
import re
import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import (
    circumference_cm_to_dbh_inches,
    emit,
    enforce_tree_schema,
    stream_to_table,
    validate_coordinates,
)
from shared.platforms.arcgis import FeatureLayer, esri_point, iter_features

LAYER = FeatureLayer(
    "https://geodata.antwerpen.be/arcgissql/rest/services/P_Portal/portal_publiek6/MapServer/676",
    timeout=300,
)
SOURCE_URL = LAYER.base
FIELDS = ("OBJECTID", "OBJECT_GUID", "LATBOOMSOORT", "STAMOMTREK")
ID_PREFIX = "antwerp-"

# Quote characters the register uses for a cultivar, all read as `'`.  `¿`
# is a closing quote lost to an encoding round trip (`` `Summit¿ ``).
_QUOTES = str.maketrans({"‘": "'", "’": "'", "`": "'", "¿": "'", "´": "'"})
# `(x)` after the name or after the cultivar: the register's hybrid mark.
_HYBRID_MARK = re.compile(r"\s*\(x\)", re.IGNORECASE)
# `Cupressocyparis leylandii (x C)`: the intergeneric mark.
_INTERGENERIC_MARK = re.compile(r"\s*\(x C\)", re.IGNORECASE)
# Any other parenthetical: synonyms (`(=Sophora)`), notes (`(bospest)`),
# trade names (`(Red Star)`, `(Gellerts Butterbirne)`).
_PARENTHETICAL = re.compile(r"\s*\([^)]*\)")
# `'Austep' FAIRVIEW FLAME`: a trade name in capitals after the cultivar.
_TRADE_NAME = re.compile(r"'\s+[A-Z][A-Z .-]+$")
# The botanical authority written in the cultivar's place.
_AUTHORITY_CULTIVARS = {"Moench"}


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def parse_species(value) -> tuple[str | None, str | None]:
    """The species string and, where the shared parser would misread it, the
    cultivar.  A `None` cultivar lets `enforce_tree_schema` parse the quote."""
    text = clean(value)
    if text is None:
        return None, None
    text = text.translate(_QUOTES)
    hybrid = bool(_HYBRID_MARK.search(text))
    text = _HYBRID_MARK.sub("", text)
    text = _INTERGENERIC_MARK.sub("", text)
    text = _PARENTHETICAL.sub("", text)
    text = _TRADE_NAME.sub("'", text)
    text = " ".join(text.split())
    if hybrid:
        genus, _, rest = text.partition(" ")
        if rest and not rest.startswith(("x ", "'")):
            text = f"{genus} x {rest}"
    first = text.find("'")
    if first == -1:
        return text, None
    name = text[:first].strip()
    last = text.rfind("'")
    # One quote is an unterminated cultivar (`'Merrill`); more than two is an
    # apostrophe inside one (`'Faassen's Black'`), which the shared parser
    # would close early.  Either way the cultivar runs to the last quote.
    selection = text[first + 1 : last] if last > first else text[first + 1 :]
    selection = selection.strip(" '") or None
    if selection in _AUTHORITY_CULTIVARS:
        selection = None
    return name or None, selection


def parse_dbh(circumference_cm) -> float | None:
    if circumference_cm is None:
        return None
    number = float(circumference_cm)
    if not math.isfinite(number) or number <= 0:
        return None
    return circumference_cm_to_dbh_inches(number)


def has_id(feature: dict) -> bool:
    return bool(clean((feature.get("attributes") or {}).get("OBJECT_GUID")))


def transform(features: list[dict]) -> pa.Table:
    tree_id: list[str] = []
    species: list[str | None] = []
    cultivar: list[str | None] = []
    latitude: list[float | None] = []
    longitude: list[float | None] = []
    dbh: list[float | None] = []

    for feature in features:
        rec = feature.get("attributes") or {}
        missing = set(FIELDS) - set(rec)
        if missing:
            raise RuntimeError(f"Antwerp ingest: layer 676 is missing field(s) {sorted(missing)}")
        guid = clean(rec["OBJECT_GUID"]).strip("{}").lower()
        lat, lon = esri_point(feature.get("geometry"))
        name, selection = parse_species(rec["LATBOOMSOORT"])
        tree_id.append(f"{ID_PREFIX}{guid}")
        species.append(name)
        cultivar.append(selection)
        latitude.append(lat)
        longitude.append(lon)
        dbh.append(parse_dbh(rec["STAMOMTREK"]))

    n = len(tree_id)
    return pa.table(
        {
            "tree_id": pa.array(tree_id, type=pa.string()),
            "city": pa.array(["BEANR"] * n, type=pa.string()),
            "species": pa.array(species, type=pa.string()),
            "cultivar": pa.array(cultivar, type=pa.string()),
            "plant_date": pa.array([None] * n, type=pa.date32()),
            "latitude": pa.array(latitude, type=pa.float64()),
            "longitude": pa.array(longitude, type=pa.float64()),
            "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
        }
    )


def iter_pages():
    yield from iter_features(
        LAYER,
        out_fields=",".join(FIELDS),
        return_geometry=True,
        out_sr=4326,
        order_by="OBJECTID",
    )


def main() -> None:
    table = stream_to_table(iter_pages(), transform, keep=has_id, label="Antwerp OpenData")
    table = validate_coordinates(table, city="Antwerp", city_code="BEANR")
    table = enforce_tree_schema(table, city="Antwerp", data_source="ANTWERPEN_OPENDATA")
    emit(table)


if __name__ == "__main__":
    main()
