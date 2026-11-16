#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""City of Port Phillip street, park and council-property trees, from data.gov.au.

Source: "City of Port Phillip Trees" (`city-of-port-phillip-trees`) on
data.gov.au's CKAN, published by the City of Port Phillip under CC BY 2.5 AU.
The CSV resource `dd8aa3f7` has an active datastore: 46,432 rows, last
modified 2022-07-28.  The package also carries a 2022-05 GeoJSON of the same
trees; the datastore is read instead because it pages, and because the CSV
file is cp1252 (a `‘` in a species note) which the datastore has already
decoded.  Port Phillip sits south of the City of Melbourne across the Yarra
and St Kilda Road (Southbank, South Melbourne, Albert Park, St Kilda, Elwood),
inside AUMEL's territory, so it is a partition of Melbourne rather than a city.

**`ref` is the per-tree id**: 46,432 distinct values over 46,432 rows, none
blank, checked over the whole resource.  `TABLE_ROW_ID` is unique too but is
the export's row number.  Ids are prefixed `pph-` so they cannot meet the
City of Melbourne's `mel-` ids or another council's.

* `species` is a botanical name (`Platanus X acerifolia`); the shared
  sanitiser folds the capital `X`, synonyms and unquoted cultivars.
* `dbh` is centimetres.  Seven rows record the open-topped class `200+`, read
  as its floor (200 cm); anything else unparseable is counted and refused above
  1%.  Zeros and absurd values (a 2,008 cm entry) are nulled by the schema.
* `crown` is the mean crown spread in metres (`crown_min`/`crown_max` are its
  two axes) and is published as `crown_width_m`.
* `planted` is `d/mm/yyyy` and almost always 1 January of a round year: a
  surveyor's year estimate, not a placeholder (median diameter falls steadily
  from 100 cm for 1900 to 3 cm for 2021), so it is published, year-precise.
* `location` (`street`/`park`/`council`) is not a site status; every row is a
  tree.  `height` has no canonical column.
"""

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
    "data.gov.au/data", "dd8aa3f7-d5c2-459b-af5c-9b31a6848455", timeout=180
)
FIELDS = "ref,lat,lon,species,dbh,crown,planted"

# The open-topped class at the top of the diameter field.
OPEN_TOP_DBH = {"200+": 200.0}
MAX_UNPARSED_DBH_SHARE = 0.01

_dbh_unparsed = 0


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


def parse_dbh_cm(value) -> float | None:
    global _dbh_unparsed
    text = clean(value)
    if text is None:
        return None
    if text in OPEN_TOP_DBH:
        return OPEN_TOP_DBH[text]
    try:
        return float(text)
    except ValueError:
        _dbh_unparsed += 1
        return None


def parse_planted(value) -> date | None:
    """`d/mm/yyyy` (and the odd `yyyy-mm-dd`) to a date; anything else null."""
    text = clean(value)
    if text is None:
        return None
    try:
        if "/" in text:
            day, month, year = (int(p) for p in text.split("/"))
        else:
            year, month, day = (int(p) for p in text.split("-"))
        return date(year, month, day)
    except ValueError:
        return None


def transform(rows: list[dict]) -> pa.Table:
    tree_id: list[str] = []
    species: list[str | None] = []
    plant_date: list[date | None] = []
    latitude: list[float | None] = []
    longitude: list[float | None] = []
    dbh: list[float | None] = []
    crown: list[float | None] = []

    for rec in rows:
        ref = clean(rec.get("ref"))
        if ref is None:
            continue
        tree_id.append(f"pph-{ref}")
        species.append(normalize_species(clean(rec.get("species"))))
        plant_date.append(parse_planted(rec.get("planted")))
        latitude.append(as_float(rec.get("lat")))
        longitude.append(as_float(rec.get("lon")))
        dbh.append(cm_to_inches(parse_dbh_cm(rec.get("dbh"))))
        crown.append(as_float(rec.get("crown")))

    n = len(tree_id)
    return pa.table({
        "tree_id": pa.array(tree_id, type=pa.string()),
        "city": pa.array(["AUMEL"] * n, type=pa.string()),
        "species": pa.array(species, type=pa.string()),
        "plant_date": pa.array(plant_date, type=pa.date32()),
        "latitude": pa.array(latitude, type=pa.float64()),
        "longitude": pa.array(longitude, type=pa.float64()),
        "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
        "crown_width_m": pa.array(crown, type=pa.float64()),
    })


def has_id(rec: dict) -> bool:
    return clean(rec.get("ref")) is not None


if __name__ == "__main__":
    pages = iter_datastore_rows(RESOURCE, fields=FIELDS)
    table = stream_to_table(pages, transform, keep=has_id, label="Port Phillip trees")
    if _dbh_unparsed > MAX_UNPARSED_DBH_SHARE * table.num_rows:
        raise RuntimeError(
            f"Port Phillip: {_dbh_unparsed} of {table.num_rows} `dbh` values did "
            "not parse; the field's coding has changed"
        )
    if _dbh_unparsed:
        print(f"Port Phillip: {_dbh_unparsed} unparseable dbh value(s) published as null", file=sys.stderr)
    table = validate_coordinates(table, city="Melbourne", city_code="AUMEL")
    table = enforce_tree_schema(table, city="Melbourne", data_source="PORT_PHILLIP")
    emit(table)
