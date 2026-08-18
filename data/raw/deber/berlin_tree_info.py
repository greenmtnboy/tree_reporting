#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///

"""
Fetch Berlin street tree data (Straßenbäume) from Berlin's official WFS service
and emit Arrow IPC to stdout.

Source: https://gdi.berlin.de/services/wfs/baumbestand
Feature type: baumbestand:strassenbaeume (434k trees)

Field mapping:
  gisid        -> tree_id  (prefixed "ber-" for global uniqueness)
  art_bot      -> species  (scientific name / Latin binomial)
  art_dtsch    -> tree_name (German common name)
  pflanzjahr   -> plant_date (year integer → date(year, 1, 1), null if 0 or missing)
  stammumfg    -> diameter_at_breast_height (trunk circumference cm → DBH inches)
  geometry     -> latitude / longitude (WGS84 Point coordinates from GeoJSON)
"""

import sys
from datetime import date
from pathlib import Path

import pyarrow as pa
sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import (
    emit,
    enforce_tree_schema,
    get_json_with_retry,
    normalize_species,
    validate_coordinates,
    parse_plant_date_year,
    circumference_cm_to_dbh_inches,
)

WFS_BASE = "https://gdi.berlin.de/services/wfs/baumbestand"
TYPENAMES = "baumbestand:strassenbaeume"
PAGE_SIZE = 10_000


def fetch_all_features() -> list[dict]:
    """Paginate through the WFS and return all GeoJSON features."""
    features = []
    start = 0
    while True:
        params = {
            "SERVICE": "WFS",
            "VERSION": "2.0.0",
            "REQUEST": "GetFeature",
            "TYPENAMES": TYPENAMES,
            "SRSNAME": "EPSG:4326",
            "outputFormat": "application/json",
            "COUNT": str(PAGE_SIZE),
            "startIndex": str(start),
        }
        # The WFS serves the platform-wide HTML maintenance page with HTTP 200,
        # so a non-JSON body here means "portal down", not "bad request".
        page = get_json_with_retry(WFS_BASE, params=params)
        batch = page.get("features", [])
        features.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
        start += PAGE_SIZE
    return features


def parse_plant_date(val) -> date | None:
    """Convert pflanzjahr (year integer or string) to a date. Returns None for 0/missing."""
    return parse_plant_date_year(val)


def parse_dbh(val) -> float | None:
    """Convert trunk circumference in cm to DBH in inches."""
    return circumference_cm_to_dbh_inches(val)


def transform(features: list[dict]) -> pa.Table:
    if not features:
        raise ValueError("Berlin WFS returned 0 features")

    tree_ids = []
    cities = []
    species_list = []
    tree_names = []
    plant_dates = []
    latitudes = []
    longitudes = []
    dbhs = []

    for f in features:
        props = f.get("properties") or {}
        geom = f.get("geometry") or {}

        raw_id = props.get("gisid") or props.get("id") or ""
        tree_ids.append(f"ber-{raw_id}" if raw_id else None)
        cities.append("DEBER")

        species_list.append(normalize_species(props.get("art_bot")))
        raw_name = props.get("art_dtsch") or ""
        tree_names.append(raw_name.strip() if raw_name.strip() else None)
        plant_dates.append(parse_plant_date(props.get("pflanzjahr")))
        dbhs.append(parse_dbh(props.get("stammumfg")))

        # GeoJSON Point geometry: [longitude, latitude]
        coords = geom.get("coordinates") if geom.get("type") == "Point" else None
        if coords and len(coords) >= 2:
            longitudes.append(float(coords[0]))
            latitudes.append(float(coords[1]))
        else:
            longitudes.append(None)
            latitudes.append(None)

    return pa.table(
        {
            "tree_id": pa.array(tree_ids, type=pa.string()),
            "city": pa.array(cities, type=pa.string()),
            "species": pa.array(species_list, type=pa.string()),
            "tree_name": pa.array(tree_names, type=pa.string()),
            "plant_date": pa.array(plant_dates, type=pa.date32()),
            "latitude": pa.array(latitudes, type=pa.float64()),
            "longitude": pa.array(longitudes, type=pa.float64()),
            "diameter_at_breast_height": pa.array(dbhs, type=pa.float64()),
        }
    )


if __name__ == "__main__":
    features = fetch_all_features()
    table = transform(features)
    table = validate_coordinates(table, city="Berlin", city_code="DEBER")
    table = enforce_tree_schema(table, city="Berlin", data_source="BERLIN_OPENDATA")
    emit(table)
