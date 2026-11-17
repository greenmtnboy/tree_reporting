#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Ghent's municipal tree inventory ("Locaties bomen Gent"), from data.stad.gent.

Source: OpenDataSoft dataset `locaties-bomen-gent` on the city's open-data
portal (https://data.stad.gent/explore/dataset/locaties-bomen-gent/),
published by the Groendienst (parks department) under the Flemish
"Modellicentie Gratis Hergebruik".  It is a direct export of the
Groendienst's tree-management system -- **68,240 rows** when this was
written.  The bulk export endpoint returns the whole layer as one ~3 MB
parquet; the records API caps its offset at 10,000 and is not used.

The publisher says, on the dataset page, that the inventory is not complete
and that refreshes are paused while the Groendienst moves to a new management
system ("actuele data ... verwacht in Q4 2026").  When that lands, the ids
below may change with it; the `@source_kept` guard will say so.

**Keyed on `gis_id`** (`fysgro/tep336429`), the management system's feature
id, verified unique over the 68,232 rows that carry one; 8 rows have none
and are dropped with a count.  Published as `ghent-tep336429`.

**Species is `sortiment`**, a scientific name with the cultivar quoted
(`Carpinus betulus 'Frans Fontaine'`), which the shared hygiene splits.  Its
notes in parentheses (`Betula pendula (verrucosa)`, `Aesculus carnea (x)`)
are stripped there too.  The register's own habits handled here: an
apostrophe inside a cultivar (`'Paul's Scarlet'`, 1.2k rows), which the
shared parser would close the quote on, is passed as the cultivar column; a
stray full stop (`Malus floribunda.`), the abbreviation `Aesculus hip.`,
three rows written family-first (`Ulmaceae zelkova 'Serrata'`), a hybrid mark
written as a trailing `(x)` (`Aesculus carnea (x)` is `Aesculus x carnea`,
~430 rows), and an unquoted capitalised cultivar (`Ulmus Lobel`).  `Te Bepalen`
("to be determined", 132 rows) publishes as `Unknown`.  About 31% of rows (largely park and woodland trees)
have no `sortiment` and publish as `Unknown`.  There is no common-name column.

**Stem size.**  `stamomtrek` is the trunk circumference in cm (38.6k rows;
median 70, nursery sizes 10-14 common on young trees).  The publisher warns it
is often the average over a street's trees of the same species, not a
per-tree measurement.  `diameter` is the trunk diameter in cm, recorded on 3k
rows, almost all in cemeteries (median 30; it rises with the `hoogte` height
band, 20 cm under 6 m to 80 cm over 24 m, so it is not a circumference and
not a crown).  25 rows carry both and disagree erratically (`451/451`,
`290/390`); the circumference, the column the inventory actually maintains,
wins.  `hoogte` is a height band, which the schema has no column for.

**Planting date** is `aanlegjaar`, a year stamped as January 1 (18k rows).
One row says year 204; `enforce_tree_schema` nulls it.

No column marks a site that is not a tree: `beheerfase` is the tree's life
stage (young, mature, veteran) and there is no felled or vacant status.
"""

import re
import sys
from datetime import date
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import (
    circumference_cm_to_dbh_inches,
    cm_to_inches,
    download_parquet,
    emit,
    enforce_tree_schema,
    parse_wkb_point,
    validate_coordinates,
)

DATASET = "locaties-bomen-gent"
FIELDS = ["gis_id", "sortiment", "aanlegjaar", "stamomtrek", "diameter", "geo_point_2d"]
SOURCE_URL = (
    f"https://data.stad.gent/api/explore/v2.1/catalog/datasets/{DATASET}/exports/parquet"
    f"?select={','.join(FIELDS)}&timezone=UTC"
)


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


# `Crataegus laevigata 'Paul's Scarlet'`: an apostrophe inside the quoted
# cultivar, which the shared parser would read as the closing quote ("Paul").
_POSSESSIVE_CULTIVAR = re.compile(r"'\s*([^']*\w's\b[^']*)'")
# `Ulmaceae zelkova 'Serrata'`: family, genus, epithet-in-quotes.
_FAMILY_FIRST = re.compile(r"^[A-Z][a-z]+aceae ([a-z]+) '([a-z]+)'$", re.IGNORECASE)
_TRAILING_HYBRID = re.compile(r"\s*\(x\)\s*$")
_BARE_CULTIVAR = re.compile(r"^([A-Z][a-z]+) ([A-Z][a-z]+)$")
# Abbreviated epithets the register writes.
_ABBREVIATIONS = {"Aesculus hip.": "Aesculus hippocastanum "}


def parse_species(value) -> tuple[str | None, str | None]:
    """The species string and, where the shared parser would misread it, the
    cultivar.  A `None` cultivar lets `enforce_tree_schema` parse the quote."""
    text = clean(value)
    if text is None:
        return None, None
    family_first = _FAMILY_FIRST.match(text)
    if family_first:
        return f"{family_first.group(1).capitalize()} {family_first.group(2).lower()}", None
    for short, full in _ABBREVIATIONS.items():
        if text.startswith(short):
            text = full + text[len(short):].lstrip()
    # `Malus floribunda.`: a stray full stop the epithet check would refuse.
    if text.endswith(".") and not text.endswith(" sp."):
        text = text[:-1]
    # `Aesculus carnea (x)`: the register's hybrid mark, written after the
    # name; the parenthetical would otherwise be stripped as a note.
    if _TRAILING_HYBRID.search(text):
        text = _TRAILING_HYBRID.sub("", text)
        genus, _, rest = text.partition(" ")
        if rest and not rest.startswith("x "):
            text = f"{genus} x {rest}"
    # `Ulmus Lobel`: a cultivar with no quotes, capitalised as cultivars are.
    bare = _BARE_CULTIVAR.match(text)
    if bare:
        text = f"{bare.group(1)} '{bare.group(2)}'"
    possessive = _POSSESSIVE_CULTIVAR.search(text)
    if possessive:
        return text[: possessive.start()].strip(), possessive.group(1).strip()
    return text, None


def parse_id(value) -> str | None:
    """`fysgro/tep336429` -> `tep336429`: the part after the system prefix."""
    text = clean(value)
    if text is None:
        return None
    return text.rsplit("/", 1)[-1] or None


def parse_dbh(circumference_cm, diameter_cm) -> float | None:
    if circumference_cm:
        return circumference_cm_to_dbh_inches(circumference_cm)
    if diameter_cm:
        return cm_to_inches(diameter_cm)
    return None


def transform(table: pa.Table) -> pa.Table:
    missing = set(FIELDS) - set(table.schema.names)
    if missing:
        raise RuntimeError(f"Ghent ingest: export is missing field(s) {sorted(missing)}")

    tree_id: list[str] = []
    species: list[str | None] = []
    cultivar: list[str | None] = []
    plant_date: list[date | None] = []
    latitude: list[float | None] = []
    longitude: list[float | None] = []
    dbh: list[float | None] = []
    no_id = 0

    for gis_id, name, planted, circumference, diameter, point in zip(
        *(table[f].to_pylist() for f in FIELDS)
    ):
        key = parse_id(gis_id)
        if key is None:
            no_id += 1
            continue
        lon, lat = parse_wkb_point(point)
        tree_id.append(f"ghent-{key}")
        name, selection = parse_species(name)
        species.append(name)
        cultivar.append(selection)
        plant_date.append(planted)
        latitude.append(lat)
        longitude.append(lon)
        dbh.append(parse_dbh(circumference, diameter))

    print(
        f"Ghent OpenData: {table.num_rows} rows; dropped {no_id} with no gis_id",
        file=sys.stderr,
    )
    return pa.table(
        {
            "tree_id": pa.array(tree_id, type=pa.string()),
            "city": pa.array(["BEGNT"] * len(tree_id), type=pa.string()),
            "species": pa.array(species, type=pa.string()),
            "cultivar": pa.array(cultivar, type=pa.string()),
            "plant_date": pa.array(plant_date, type=pa.date32()),
            "latitude": pa.array(latitude, type=pa.float64()),
            "longitude": pa.array(longitude, type=pa.float64()),
            "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
        }
    )


def main() -> None:
    table = transform(pq.read_table(download_parquet(SOURCE_URL)))
    table = validate_coordinates(table, city="Ghent", city_code="BEGNT")
    table = enforce_tree_schema(table, city="Ghent", data_source="GHENT_OPENDATA")
    emit(table)


if __name__ == "__main__":
    main()
