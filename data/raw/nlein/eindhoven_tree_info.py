#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Eindhoven's municipal tree inventory ("Bomen"), from data.eindhoven.nl.

Source: OpenDataSoft dataset `bomen` on the city's own open-data portal
(https://data.eindhoven.nl/explore/dataset/bomen/), published by Gemeente
Eindhoven, whose `license` field states "Publiek domein" (public domain).
It mirrors the city's `GRN_Bomen` MapServer
(gisservice.eindhoven.nl/arcgis/rest/services/GRN_Bomen/MapServer/0, the
record's `references`), refreshed weekly ("Wekelijks").  **127,887 rows** on
2026-11-17.  The parquet export returns the whole layer as one ~12 MB file;
the records API caps its offset at 10,000 and is not used.

"Alle bomen in beheer van Gemeente Eindhoven": the trees the city manages,
**plus private trees** the 2024 tree bye-law registers as valuable or
monumental.  `eigenaar` (owner) is `BOR` (Beheer Openbare Ruimte, the public
space department, 108k), `Particulier` (private, 11.5k), null (6.7k, managed
by BOR on nearly every row), `Begraafplaats` (municipal cemeteries),
`Grond en Vastgoed` (the city's land and property department) and a handful
of others.  **Private trees are dropped** with a count; every other owner,
null included, is a public tree and kept.  The MapServer's
`BOOM_DEF_AFWEZIG` ("tree definitively absent") is never true, so the layer
carries no removed trees; `eindbeeld` `Gerooid` ("uprooted", 26 rows) is a
felled tree still in the layer and is dropped.

**Keyed on `boomnummer`**, the city's tree number, verified non-null and
distinct over all 127,887 rows (2 to 205,477 with gaps).  `objectid` is a
dense 1..n renumbering of the export; `geovisia_id` (a GUID from the city's
Geovisia system) is unique too, but the tree number is the one the city's
own map and bye-law use.  Published as `eindhoven-{boomnummer}`.

**Species is `boomsoort`**, a scientific binomial without the hybrid mark
(`Platanus hispanica`, `Tilia europaea`, `Ulmus hollandica`), which the
shared synonym map folds to the accepted `x` names.  The cultivar is its own
column, `boomsoort_varieteit`, with the placeholders `Onbekend` ("unknown"),
`Geen` ("none") and `<macro:leeg>` ("empty").  The register's own habits
handled here: a cultivar written as the epithet (`Prunus accolade`,
`Prunus kursar`, `Prunus pandora`, `Ulmus resista`, `Populus astria`,
`Tilia cordata böhlje`), a capitalised epithet (`Amelanchier Lamarckii`), a
species name in the cultivar column (`Malus tschonoskii` / `Malus
Tschonoskii`), a trade name before the cultivar (`LUTECE ('Nanguen')`), and
a few misspelt cultivars (`CULTIVAR_FIXES`).  `Onbekend` and null publish as
`Unknown`.  `boomsoort_nederlands`, the Dutch common name, is not read.

**No stem size.**  The layer publishes no circumference or diameter;
`hoogte` is a height band (`0-6` ... `>24` m), which the schema has no
column for, and `eindbeeld` is the pruning form.

**Planting date** is `plantjaar`, a year published as 1 January of it.  The
round-decade spikes (1970: 12.9k, 1980: 10.4k, 1960: 6.9k, with 1975 and
1985 beside them) are estimated plantings of a surveyed stock, not one
stamped default, and are kept, as Utrecht's are.  The one impossible value
(year 190) is nulled by the schema.

**Extent.**  1,791 BOR-owned trees line the Eindhovensch Kanaal east of
the city (51.446-51.458 N, 5.58-5.67 E), outside the municipality's
boundary (which stops at 5.55 E) in Nuenen and Geldrop-Mierlo;
`CITY_BOUNDS` ends at 5.58 E, so `validate_coordinates` drops them with a
count.
"""

import re
import sys
from collections import Counter
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import (
    download_parquet,
    emit,
    enforce_tree_schema,
    parse_wkb_point,
    validate_coordinates,
)

DATASET = "bomen"
FIELDS = [
    "boomnummer",
    "eigenaar",
    "boomsoort",
    "boomsoort_varieteit",
    "eindbeeld",
    "plantjaar",
    "geo_point_2d",
]
SOURCE_URL = (
    f"https://data.eindhoven.nl/api/explore/v2.1/catalog/datasets/{DATASET}/exports/parquet"
    f"?select={','.join(FIELDS)}&timezone=UTC"
)
ID_PREFIX = "eindhoven-"

PRIVATE_OWNER = "particulier"
# `eindbeeld` values that say the tree is gone.
FELLED_FORMS = frozenset({"gerooid"})
# Cultivar-column placeholders: "unknown", "none", "empty".
CULTIVAR_PLACEHOLDERS = frozenset({"onbekend", "geen", "<macro:leeg>", "nvt", "n.v.t."})
# Epithets in `boomsoort` that are really cultivars: Prunus 'Accolade',
# 'Kursar' and 'Pandora' are hybrid cherries with no species epithet;
# `Ulmus resista` is the Resista series of elm cultivars (its rows carry
# 'New Horizon' as the cultivar); Populus 'Astria' is a poplar clone;
# `Tilia cordata böhlje` is T. cordata 'Böhlje'.
EPITHET_IS_CULTIVAR = {
    ("prunus", "accolade"): "Accolade",
    ("prunus", "kursar"): "Kursar",
    ("prunus", "pandora"): "Pandora",
    ("ulmus", "resista"): "Resista",
    ("populus", "astria"): "Astria",
}
# Misspelt or variant cultivars, one or a few dozen rows each.
CULTIVAR_FIXES = {
    "Zwitser Gloie": "Zwitsers Glorie",
    "Purprea": "Purpurea",
    "Apallachia": "Appalachia",
    "Mavellous": "Marvellous",
    "Bohlje": "Böhlje",
    "W.Van Haamstede": "W. van Haamstede",
    "W.van Haamstede": "W. van Haamstede",
    "New horizon": "New Horizon",
    "San zanobi": "San Zanobi",
    "landscape bloom": "Landscape Bloom",
    "kordia": "Kordia",
    "Purple fountain": "Purple Fountain",
}
# `LUTECE ('Nanguen')`: an all-caps trade name with the cultivar in brackets.
_TRADE_NAME = re.compile(r"^[A-Z][A-Z\s®]+\(\s*'([^']+)'\s*\)$")


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def parse_cultivar(value, genus: str | None) -> str | None:
    text = clean(value)
    if text is None or text.lower() in CULTIVAR_PLACEHOLDERS:
        return None
    trade = _TRADE_NAME.match(text)
    if trade:
        text = trade.group(1).strip()
    text = text.strip("'\"‘’ ")
    # `Malus Tschonoskii` in the cultivar column of a `Malus tschonoskii`
    # row: the species again, not a cultivar.
    if genus and text.lower().startswith(genus.lower() + " "):
        return None
    return CULTIVAR_FIXES.get(text, text) or None


def parse_species(value) -> tuple[str | None, str | None]:
    """(species string, cultivar found in the species column)."""
    text = clean(value)
    if text is None:
        return None, None
    words = text.split(" ")
    genus = words[0].capitalize()
    if len(words) == 1:
        return genus, None
    epithet = words[1].lower()
    if (genus.lower(), epithet) in EPITHET_IS_CULTIVAR and len(words) == 2:
        return genus, EPITHET_IS_CULTIVAR[(genus.lower(), epithet)]
    if epithet == "x" and len(words) >= 3:
        return f"{genus} x {words[2].lower()}", None
    if len(words) == 3:
        # `Tilia cordata böhlje`: a lowercase cultivar after the epithet.
        return f"{genus} {epithet}", words[2].capitalize()
    return f"{genus} {' '.join([epithet, *words[2:]])}", None


def transform(table: pa.Table) -> pa.Table:
    missing = set(FIELDS) - set(table.schema.names)
    if missing:
        raise RuntimeError(f"Eindhoven ingest: export is missing field(s) {sorted(missing)}")

    tree_id: list[str] = []
    species: list[str | None] = []
    cultivar: list[str | None] = []
    plant_date = []
    latitude: list[float | None] = []
    longitude: list[float | None] = []
    dropped: Counter[str] = Counter()

    for key, owner, soort, variety, form, planted, point in zip(
        *(table[f].to_pylist() for f in FIELDS)
    ):
        if key is None:
            dropped["no id"] += 1
            continue
        if (clean(owner) or "").lower() == PRIVATE_OWNER:
            dropped["private (eigenaar Particulier)"] += 1
            continue
        if (clean(form) or "").lower() in FELLED_FORMS:
            dropped["felled (eindbeeld Gerooid)"] += 1
            continue
        name, from_species = parse_species(soort)
        lon, lat = parse_wkb_point(point)
        tree_id.append(f"{ID_PREFIX}{key}")
        species.append(name)
        cultivar.append(parse_cultivar(variety, name.split(" ")[0] if name else None) or from_species)
        plant_date.append(planted)
        latitude.append(lat)
        longitude.append(lon)

    print(
        f"Eindhoven OpenData: {table.num_rows} rows; kept {len(tree_id)}; dropped "
        + (", ".join(f"{n} {why}" for why, n in dropped.items()) or "none"),
        file=sys.stderr,
    )
    return pa.table(
        {
            "tree_id": pa.array(tree_id, type=pa.string()),
            "city": pa.array(["NLEIN"] * len(tree_id), type=pa.string()),
            "species": pa.array(species, type=pa.string()),
            "cultivar": pa.array(cultivar, type=pa.string()),
            "plant_date": pa.array(plant_date, type=pa.date32()),
            "latitude": pa.array(latitude, type=pa.float64()),
            "longitude": pa.array(longitude, type=pa.float64()),
            "diameter_at_breast_height": pa.array([None] * len(tree_id), type=pa.float64()),
        }
    )


def main() -> None:
    table = transform(pq.read_table(download_parquet(SOURCE_URL)))
    table = validate_coordinates(table, city="Eindhoven", city_code="NLEIN")
    table = enforce_tree_schema(table, city="Eindhoven", data_source="EINDHOVEN_OPENDATA")
    emit(table)


if __name__ == "__main__":
    main()
