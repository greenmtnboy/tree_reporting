#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Adelaide's municipal street trees, from the City of Adelaide's open data.

Source: "Street Trees" (`street-trees`) on data.sa.gov.au, published by the
City of Adelaide under CC BY 4.0.  The CKAN package points at a CSV the
council's FTP drop writes to S3 (`Street_Trees.csv`, ~1.2 MB); the datastore
copy CKAN keeps of it is not read, because the S3 object is the file the
council publishes and the one the freshness probe stamps.

Measured 2026-11-16 (file last modified 2025-11-10): 6,031 records plus one
wholly blank trailing row.  This is the street-tree register of the City of
Adelaide LGA only (the CBD and North Adelaide, between -34.940 and -34.902 and
138.583 to 138.618), not the Park Lands' trees.

Field choices:

  * `tree_id` <- `Asset ID (Identifier)`, the council's asset number, checked
    unique and non-null over all 6,031 records.  The blank trailing row is the
    only one without it and is dropped (logged).
  * `species` <- `Species Name (Inspection)`, which is the scientific name in
    Title Case ("Platanus X Acerifolia"); `normalize_species` lowers the
    epithet.  32 rows are blank and publish as `Unknown`.  One value,
    `Callistemon Harkness'`, has lost the opening quote of the cultivar
    'Harkness' and would otherwise read as the invented species
    "Callistemon harkness"; `SOURCE_SPELLING_FIXES` restores the quote.  The
    `Common Name` column is not mapped (common names come from enrichment).
  * `diameter_at_breast_height` <- `Circum (Inspection)`, a trunk
    circumference *band* in metres ("0m -1m", "1m - 2m", "2m - 3m", ">3m";
    233 blank).  Each band publishes its midpoint, converted with
    `circumference_cm_to_dbh_inches`; the open ">3m" band takes 3.5 m, its
    lower bound plus half the common width (Denver's rule for its open
    bucket).  A midpoint is wrong per tree by up to half a band and right in
    aggregate, which is what the size charts ask of it.
  * `latitude`/`longitude` <- `Geometry`, a WKT point in Web Mercator
    (EPSG:3857), inverted here in closed form.  It agrees with the MGA zone 54
    `Easting`/`Northing` columns to within a micrometre on all 6,029 rows that
    carry both, so no projection library is needed.
  * `plant_date` is null: the file carries an age class (`Mature`, `Semi
    mature`, ...) and no date.  Height, vigour and structure are not part of
    the canonical schema.
"""

import math
import sys
from datetime import date
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import (
    circumference_cm_to_dbh_inches,
    emit,
    enforce_tree_schema,
    iter_csv_row_chunks,
    normalize_species,
    stream_to_table,
    validate_coordinates,
)

# Kept in step with adelaide_update_time.py, which HEADs the same object.
SOURCE_URL = (
    "https://s3.ap-southeast-2.amazonaws.com/dmzweb.adelaidecitycouncil.com/"
    "OpenData/Street_Trees/Street_Trees.csv"
)

ID_FIELD = "Asset ID (Identifier)"
SPECIES_FIELD = "Species Name (Inspection)"
CIRCUMFERENCE_FIELD = "Circum (Inspection)"
GEOMETRY_FIELD = "Geometry"
REQUIRED_FIELDS = (ID_FIELD, SPECIES_FIELD, CIRCUMFERENCE_FIELD, GEOMETRY_FIELD)

# Circumference band (spaces removed) -> midpoint in centimetres.
CIRCUMFERENCE_BAND_CM: dict[str, float] = {
    "0m-1m": 50.0,
    "1m-2m": 150.0,
    "2m-3m": 250.0,
    ">3m": 350.0,
}

# A circumference label outside the four bands is counted, and above this share
# of rows the run refuses to publish: a new band spelling would otherwise null
# the diameter of every tree that carries it without anyone noticing.
MAX_UNPARSED_FRACTION = 0.01

# Source formatting slips that are not misspellings of a name (those belong in
# SPECIES_MISSPELLINGS) but a broken cultivar quote.
SOURCE_SPELLING_FIXES: dict[str, str] = {
    "Callistemon Harkness'": "Callistemon 'Harkness'",
}

WEB_MERCATOR_RADIUS_M = 6378137.0

_unparsed = {"circumference": 0, "geometry": 0, "rows": 0}


def web_mercator_to_wgs84(x: float, y: float) -> tuple[float, float]:
    """EPSG:3857 metres to (lat, lon) degrees on the WGS84 sphere it assumes."""
    lon = math.degrees(x / WEB_MERCATOR_RADIUS_M)
    lat = math.degrees(2 * math.atan(math.exp(y / WEB_MERCATOR_RADIUS_M)) - math.pi / 2)
    return lat, lon


def parse_point(value: str | None) -> tuple[float | None, float | None]:
    """`POINT (x y)` in Web Mercator to (lat, lon), or (None, None)."""
    text = (value or "").strip()
    if not text:
        return None, None
    if not text.upper().startswith("POINT") or "(" not in text:
        _unparsed["geometry"] += 1
        return None, None
    parts = text[text.find("(") + 1 : text.rfind(")")].split()
    try:
        x, y = float(parts[0]), float(parts[1])
    except (IndexError, ValueError):
        _unparsed["geometry"] += 1
        return None, None
    return web_mercator_to_wgs84(x, y)


def parse_circumference(value: str | None) -> float | None:
    """A circumference band label to the midpoint DBH in inches, or None."""
    label = "".join((value or "").split())
    if not label:
        return None
    cm = CIRCUMFERENCE_BAND_CM.get(label)
    if cm is None:
        _unparsed["circumference"] += 1
        return None
    return circumference_cm_to_dbh_inches(cm)


def has_asset_id(record: dict) -> bool:
    return bool((record.get(ID_FIELD) or "").strip())


def iter_row_chunks():
    """The CSV in chunks, after checking the header still names our fields."""
    for chunk in iter_csv_row_chunks(SOURCE_URL, delimiter=","):
        if chunk:
            missing = [f for f in REQUIRED_FIELDS if f not in chunk[0]]
            if missing:
                raise RuntimeError(
                    f"Adelaide street-tree CSV no longer has {missing}; "
                    f"header is {sorted(chunk[0])}"
                )
        yield chunk


def transform(rows: list[dict]) -> pa.Table:
    tree_id: list[str | None] = []
    species: list[str | None] = []
    plant_date: list[date | None] = []
    latitude: list[float | None] = []
    longitude: list[float | None] = []
    dbh: list[float | None] = []

    for rec in rows:
        _unparsed["rows"] += 1
        raw_species = (rec.get(SPECIES_FIELD) or "").strip()
        raw_species = SOURCE_SPELLING_FIXES.get(raw_species, raw_species)
        lat, lon = parse_point(rec.get(GEOMETRY_FIELD))

        tree_id.append(f"adl-{rec[ID_FIELD].strip()}")
        species.append(normalize_species(raw_species))
        plant_date.append(None)
        latitude.append(lat)
        longitude.append(lon)
        dbh.append(parse_circumference(rec.get(CIRCUMFERENCE_FIELD)))

    return pa.table({
        "tree_id": pa.array(tree_id, type=pa.string()),
        "city": pa.array(["AUADL"] * len(tree_id), type=pa.string()),
        "species": pa.array(species, type=pa.string()),
        "plant_date": pa.array(plant_date, type=pa.date32()),
        "latitude": pa.array(latitude, type=pa.float64()),
        "longitude": pa.array(longitude, type=pa.float64()),
        "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
    })


def check_parse_failures() -> None:
    rows = _unparsed["rows"] or 1
    for what in ("circumference", "geometry"):
        count = _unparsed[what]
        if count:
            print(f"Adelaide ingest: {count} unreadable {what} value(s)", file=sys.stderr)
        if count / rows > MAX_UNPARSED_FRACTION:
            raise RuntimeError(
                f"Adelaide ingest: {count} of {rows} rows have an unreadable "
                f"{what}; the source format has changed"
            )


if __name__ == "__main__":
    table = stream_to_table(
        iter_row_chunks(), transform, keep=has_asset_id, label="Adelaide OpenData"
    )
    check_parse_failures()
    table = validate_coordinates(table, city="Adelaide", city_code="AUADL")
    table = enforce_tree_schema(
        table, city="Adelaide", data_source="ADELAIDE_OPENDATA"
    )
    emit(table)
