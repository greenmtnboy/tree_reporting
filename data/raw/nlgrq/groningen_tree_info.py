#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Groningen's municipal tree inventory ("Bomen"), from the city's GeoServer.

Source: the WFS feature type `geo-data:Bomen gemeente Groningen` on
maps.groningen.nl/geoserver, catalogued on the city's open-data portal as
"Bomen" (https://data.groningen.nl/dataset/bomen/04da3775-07f0-4388-bc12-7cd7536b04cd,
harvested to data.overheid.nl as `groningen-bomen-gemeente-groningen`),
published by Gemeente Groningen under **CC BY 4.0** ("CC-BY (4.0)",
http://creativecommons.org/licenses/by/4.0/deed.nl, on every distribution),
refreshed monthly.  "Alle bomen in beheer bij de gemeente Groningen": the
trees the city manages, across the whole gemeente including the former
municipalities of Haren and Ten Boer merged into it in 2019.  **126,016
rows** on 2026-11-20.  Read as GeoJSON through WFS 2.0 paging
(`shared.platforms.wfs`) in EPSG:4326, which GeoServer reprojects from the
layer's RD New; the layer's own `LAT`/`LON` strings agree to ~10 cm and are
not read.

**Keyed on `OBJECT`**, the city's object number in its asset system:
non-null and distinct over all 126,016 rows, 2,094,004 to 4,470,585 with
gaps (not a dense row number), and the number the city's bomenkaart shows.
The WFS feature id (`fid--39d7cffa_...`) is a GeoServer session artefact
that changes between requests and is not used.  Published as
`groningen-{OBJECT}`.

**Species is `LATIJNSE_NAAM`**, the scientific name with a quoted cultivar
(`Tilia x europaea 'Koningslinde'`, curly quotes on a few rows);
`BOOMSOORT`, the Dutch common name, is not read.  `parse_name` splits the
cultivar off between the first and last quote (the shared parser stops at the
first closing quote, which would cut `'Westhof's Glorie'`, 2,537 ashes),
drops a Dutch note in parentheses (`(CV onbekend)`, "cultivar unknown") and
an all-caps trade name (`Ulmus 'Morton Glossy' TRIUMPH`).  The export loses
a genus's leading `Z` (`elkova serrata`, 62 rows; `anthoxylum`, `iziphus`),
restored by `GENUS_FIXES`.  An empty name (155 rows) and the unexplained
code `WW` (25 rows, no common name either) publish as `Unknown`.

**Not trees**: `Verwijderd Kastanjebloedingsziekte` ("removed, bleeding
canker", 69) and `Verwijderd Storm` ("removed, storm", 26) are felled trees
still in the layer, and are dropped.  `EIGENAAR` (owner) is `Gemeente` on
all but 42 rows; the 33 `Particulier` (private) trees are dropped, as
Eindhoven's are; `Prorail` (9, the rail-infrastructure agency, managed by
the city) is kept.

**`KIEMJAAR` is the germination year**, the Dutch tree-management estimate
of a tree's age (a nursery tree is planted some years after it germinates),
published as 1 January of it in `plant_date` because the age it implies is
what the column is used for.  The round five-year spikes (1980: 14.6k,
1985, 1990, 1970) are estimates of a surveyed stock, kept as Utrecht's and
Eindhoven's are.  `0` (15 rows) is nulled here, and the impossible `190`,
`2090` and `2098` by the schema.

**No stem size.**  The layer publishes no diameter or circumference;
`BOOMHOOGTE` is a height band (`tot 6 m.` ... `24 m. en hoger`), which the
schema has no column for.  `MONUMENTAAL` (34k `Monumentale boom`) is a
protection status, not a name, and is not read.

**Extent.**  The gemeente reaches 53.313 N at Ten Post and Sint Annen (PDOK
`Gemeentegebied` GM0014); `CITY_BOUNDS` must reach past it or
`validate_coordinates` drops those ~355 trees.
"""

import re
import sys
from collections import Counter
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import (
    UNKNOWN_SPECIES,
    emit,
    enforce_tree_schema,
    parse_plant_date_year,
    stream_to_table,
    validate_coordinates,
)
from shared.platforms.wfs import WfsLayer, iter_wfs_features

LAYER = WfsLayer(
    "https://maps.groningen.nl/geoserver/geo-data/ows",
    "geo-data:Bomen gemeente Groningen",
    timeout=180,
)
PROPERTIES = ["OBJECT", "LATIJNSE_NAAM", "KIEMJAAR", "EIGENAAR", "GEOM"]
ID_PREFIX = "groningen-"

PRIVATE_OWNERS = frozenset({"particulier"})
# `LATIJNSE_NAAM` values that say the tree is gone: "removed (bleeding
# canker)", "removed (storm)".
REMOVED_PREFIX = "verwijderd"
# Values that record no identification.
UNIDENTIFIED = frozenset({"ww", "onbekend", "niet bekend", "niet bepaald"})
# The export drops the leading `Z` of a genus.
GENUS_FIXES = {
    "elkova": "Zelkova",
    "anthoxylum": "Zanthoxylum",
    "iziphus": "Ziziphus",
    "julans": "Juglans",
    "pyrus": "Pyrus",  # `PYRUS`, all caps
}
# A rank written inside the cultivar quotes.
NAME_FIXES = {
    "Acer tataricum 'subsp. Ginnala'": "Acer tataricum subsp. ginnala",
}

QUOTES = "'‘’‚‛`"
PLACEHOLDER_CULTIVARS = frozenset({"cv", "cv.", "sp", "sp.", "cv onbekend"})
# A trailing Dutch note in parentheses: `(CV onbekend)`, `(Frank Jr)`.
TRAILING_NOTE = re.compile(r"\s*\([^()]*\)\s*$")

dropped: Counter[str] = Counter()
bad_years: Counter[str] = Counter()


def parse_name(raw: str | None) -> tuple[str | None, str | None]:
    """`(species text, cultivar)` from one `LATIJNSE_NAAM` value.

    The species text still goes through `enforce_tree_schema`'s sanitiser,
    which reduces it to the accepted binomial (dropping an author such as
    `Tilia tomentosa Moensch` or an infraspecific epithet).
    """
    if raw is None:
        return UNKNOWN_SPECIES, None
    text = " ".join(str(raw).split())
    if not text or text.lower() in UNIDENTIFIED:
        return UNKNOWN_SPECIES, None
    text = NAME_FIXES.get(text, text)
    text = TRAILING_NOTE.sub("", text)

    starts = [i for i, ch in enumerate(text) if ch in QUOTES]
    cultivar = None
    head = text
    if starts:
        first, last = starts[0], starts[-1]
        head = text[:first]
        inner = text[first + 1 : last] if last > first else text[first + 1 :]
        inner = " ".join(inner.split()).strip(" -")
        if inner and inner.lower() not in PLACEHOLDER_CULTIVARS and any(c.isalpha() for c in inner):
            cultivar = inner
    # Drop an all-caps trade name (`TRIUMPH`), but not an all-caps genus.
    words = head.split()
    words = words[:1] + [w for w in words[1:] if not (len(w) > 2 and w.isupper())]
    if not words:
        return UNKNOWN_SPECIES, cultivar
    genus = GENUS_FIXES.get(words[0].lower(), words[0])
    return " ".join([genus, *words[1:]]), cultivar


def keep(feature: dict) -> bool:
    props = feature.get("properties") or {}
    if props.get("OBJECT") in (None, ""):
        dropped["no OBJECT id"] += 1
        return False
    name = " ".join(str(props.get("LATIJNSE_NAAM") or "").split()).lower()
    if name.startswith(REMOVED_PREFIX):
        dropped["removed (Verwijderd ...)"] += 1
        return False
    if (props.get("EIGENAAR") or "").strip().lower() in PRIVATE_OWNERS:
        dropped["private (EIGENAAR Particulier)"] += 1
        return False
    if not (feature.get("geometry") or {}).get("coordinates"):
        dropped["no geometry"] += 1
        return False
    return True


def germination_year(value):
    if value in (None, ""):
        return None
    try:
        year = int(value)
    except (TypeError, ValueError):
        bad_years[str(value)] += 1
        return None
    return parse_plant_date_year(year)


def transform(features: list[dict]) -> pa.Table:
    tree_id, species, cultivar, plant_date, latitude, longitude = [], [], [], [], [], []
    for feature in features:
        props = feature.get("properties") or {}
        lon, lat = feature["geometry"]["coordinates"][:2]
        name, selection = parse_name(props.get("LATIJNSE_NAAM"))
        tree_id.append(f"{ID_PREFIX}{str(props['OBJECT']).strip()}")
        species.append(name)
        cultivar.append(selection)
        plant_date.append(germination_year(props.get("KIEMJAAR")))
        latitude.append(float(lat))
        longitude.append(float(lon))
    return pa.table(
        {
            "tree_id": pa.array(tree_id, type=pa.string()),
            "city": pa.array(["NLGRQ"] * len(tree_id), type=pa.string()),
            "species": pa.array(species, type=pa.string()),
            "cultivar": pa.array(cultivar, type=pa.string()),
            "plant_date": pa.array(plant_date, type=pa.date32()),
            "latitude": pa.array(latitude, type=pa.float64()),
            "longitude": pa.array(longitude, type=pa.float64()),
            "diameter_at_breast_height": pa.array([None] * len(tree_id), type=pa.float64()),
        }
    )


def main() -> None:
    table = stream_to_table(
        iter_wfs_features(LAYER, sort_by="OBJECT", properties=PROPERTIES),
        transform,
        keep=keep,
        label="Groningen OpenData",
    )
    unreadable = sum(bad_years.values())
    if unreadable > 0.01 * table.num_rows:
        raise RuntimeError(
            f"Groningen ingest: {unreadable} KIEMJAAR values unreadable "
            f"(e.g. {bad_years.most_common(5)}); the field changed shape"
        )
    print(
        "Groningen OpenData: dropped "
        + (", ".join(f"{n} {why}" for why, n in dropped.items()) or "none")
        + (f"; {unreadable} unreadable KIEMJAAR nulled" if unreadable else ""),
        file=sys.stderr,
    )
    table = validate_coordinates(table, city="Groningen", city_code="NLGRQ")
    table = enforce_tree_schema(table, city="Groningen", data_source="GRONINGEN_OPENDATA")
    emit(table)


if __name__ == "__main__":
    main()
