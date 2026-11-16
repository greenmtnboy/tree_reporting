#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""City of Yarra street and park trees, from data.gov.au.

Source: "City of Yarra street and park trees" (`yarra-street-park-trees`) on
data.gov.au's CKAN, published by the City of Yarra under CC BY 4.0.  The CSV
resource `ade7df10` has an active datastore: 20,782 rows, last modified
2019-08-01, and nearly every row's `updated` is 2016 -- a snapshot of the
council's 2016 survey, not a maintained feed.  Yarra (Richmond, Fitzroy,
Collingwood, Abbotsford, Carlton North, Alphington) adjoins the City of
Melbourne along Hoddle Street/Punt Road's west side, Nicholson Street and
Victoria Parade, inside AUMEL's territory, so it is a partition of Melbourne
rather than a city.

**`ref` is the per-tree id**: 20,782 distinct over 20,782 rows, none blank,
checked over the whole resource.  Prefixed `yar-`.

* **`species` is abbreviated to the genus initial** (`P. x acerifolia`,
  `C. 'Kings Park Special'`), with the genus in its own column.  The initial
  is expanded from `genus` (every initial matches its genus); a blank
  `species` (126 rows) publishes the genus alone.  `Stump` is a genus value
  here and the shared rule drops it.
* `dbh` is centimetres.  `crown`, `planted`, `year_min`/`year_max`,
  `location` and `family` are blank on every row.
* `lat, lon` is one text column holding `"lat, lon"`.  Because its name has a
  comma, the datastore read asks for every field rather than a `fields` list.
"""

import re
import sys
from datetime import date
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.ckan import CkanResource, iter_datastore_rows
from shared.ingest import (
    cm_to_inches,
    emit,
    enforce_tree_schema,
    normalize_species,
    stream_to_table,
    validate_coordinates,
)

RESOURCE = CkanResource(
    "data.gov.au/data", "ade7df10-867a-42a7-adeb-99570c36ae83", timeout=180
)
LATLON_FIELD = "lat, lon"

_ABBREVIATED = re.compile(r"^([A-Z])\.\s*(.*)$")


def clean(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def as_float(value) -> float | None:
    text = clean(value)
    if text is None:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def scientific_name(genus, species) -> str | None:
    """`Platanus` + `P. x acerifolia` -> `Platanus x acerifolia`.

    A species value that is not abbreviated is taken as written; one whose
    initial does not match the genus is too (it never happens in this
    release), rather than welding a genus onto someone else's epithet.
    """
    genus = clean(genus)
    species = clean(species)
    if species is None:
        return genus
    match = _ABBREVIATED.match(species)
    if match and genus and genus[0].upper() == match.group(1):
        rest = match.group(2).strip()
        return f"{genus} {rest}" if rest else genus
    if genus and not species.lower().startswith(genus.lower()) and species[0].islower():
        return f"{genus} {species}"
    return species


def parse_lat_lon(value) -> tuple[float | None, float | None]:
    text = clean(value)
    if text is None or "," not in text:
        return None, None
    lat, lon = text.split(",", 1)
    return as_float(lat), as_float(lon)


def transform(rows: list[dict]) -> pa.Table:
    tree_id: list[str] = []
    species: list[str | None] = []
    latitude: list[float | None] = []
    longitude: list[float | None] = []
    dbh: list[float | None] = []

    for rec in rows:
        ref = clean(rec.get("ref"))
        if ref is None:
            continue
        lat, lon = parse_lat_lon(rec.get(LATLON_FIELD))
        tree_id.append(f"yar-{ref}")
        species.append(normalize_species(scientific_name(rec.get("genus"), rec.get("species"))))
        latitude.append(lat)
        longitude.append(lon)
        dbh.append(cm_to_inches(as_float(rec.get("dbh"))))

    n = len(tree_id)
    plant_date: list[date | None] = [None] * n
    return pa.table({
        "tree_id": pa.array(tree_id, type=pa.string()),
        "city": pa.array(["AUMEL"] * n, type=pa.string()),
        "species": pa.array(species, type=pa.string()),
        "plant_date": pa.array(plant_date, type=pa.date32()),
        "latitude": pa.array(latitude, type=pa.float64()),
        "longitude": pa.array(longitude, type=pa.float64()),
        "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
    })


def has_id(rec: dict) -> bool:
    return clean(rec.get("ref")) is not None


if __name__ == "__main__":
    pages = iter_datastore_rows(RESOURCE)
    table = stream_to_table(pages, transform, keep=has_id, label="Yarra trees")
    table = validate_coordinates(table, city="Melbourne", city_code="AUMEL")
    table = enforce_tree_schema(table, city="Melbourne", data_source="YARRA")
    emit(table)
