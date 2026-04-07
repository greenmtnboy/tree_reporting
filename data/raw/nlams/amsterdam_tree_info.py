#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///

"""
Fetch Amsterdam tree data from the Amsterdam REST API and emit Arrow IPC to stdout.

Source: https://api.data.amsterdam.nl/v1/bomen/stamgegevens/
API:    https://api.data.amsterdam.nl/v1/bomen/stamgegevens/?_format=json&page_size=2000

Field mapping:
  id                  -> tree_id  (prefixed "ams-" for global uniqueness)
  soortnaamLatijn     -> species  (scientific name only, normalised)
  soortnaamNederlands -> tree_name (Dutch common name)
  plantjaar           -> plant_date (year integer → January 1 of that year)
  stamdiameterklasse  -> diameter_at_breast_height (midpoint of class range, cm → inches)
  geometrie           -> latitude, longitude (WGS84 via GeoJSON or RD→WGS84 conversion)

Coordinate notes:
  The API may return geometrie as a GeoJSON Point dict with WGS84 coordinates or as
  Rijksdriehoek (RD New, EPSG:28992) x/y values. Both cases are handled:
  - If geometrie is a dict with type="Point" and coordinates=[lon, lat], use directly.
  - If geometrie contains "rdx"/"rdy" style keys or the raw JSON has "xcoordinaat"/"ycoordinaat",
    convert from RD New to WGS84 using the polynomial approximation.
"""

import sys
import requests
import pyarrow as pa
from datetime import date
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import (
    emit,
    normalize_species,
    validate_coordinates,
    get_with_retry,
    rd_to_wgs84,
    parse_plant_date_year,
)

BASE_URL = "https://api.data.amsterdam.nl/v1/bomen/stamgegevens/"
# The DSO-API blocks requests beyond page 100. Amsterdam has ~280k trees, so
# PAGE_SIZE=2000 required 140 pages and hit the 403 at page 101. 10000 per
# page keeps us at ~28 pages, safely under the limit.
PAGE_SIZE = 10000


def parse_plant_date(year: int | str | None) -> date | None:
    """Convert a planted year to January 1 of that year, or None."""
    return parse_plant_date_year(year)


# Stamdiameterklasse is a Dutch diameter-class string like "0 t/m 20 cm",
# "21 t/m 40 cm", "41 t/m 60 cm", etc.  We use the midpoint of the range
# and convert cm → inches.  Unknown/None → None.
_DIAM_CLASS_MIDPOINTS_CM: dict[str, float] = {
    "0 t/m 20 cm": 10.0,
    "21 t/m 40 cm": 30.5,
    "41 t/m 60 cm": 50.5,
    "61 t/m 80 cm": 70.5,
    "81 t/m 100 cm": 90.5,
    "101 t/m 120 cm": 110.5,
    "121 t/m 140 cm": 130.5,
    "141 t/m 160 cm": 150.5,
    "> 160 cm": 180.0,
}


def parse_dbh(diam_class: str | None) -> float | None:
    """Convert stamdiameterklasse string to DBH in inches."""
    if not diam_class:
        return None
    cm = _DIAM_CLASS_MIDPOINTS_CM.get(diam_class.strip())
    if cm is None:
        # Try to parse a bare numeric cm value (some records may differ)
        try:
            cm = float(diam_class)
        except (ValueError, TypeError):
            return None
    return cm / 2.54


def extract_coords(geometrie: dict | None) -> tuple[float | None, float | None]:
    """
    Extract (lat, lon) from the geometrie field.

    The Amsterdam API returns geometrie as a GeoJSON-style dict:
      {"type": "Point", "coordinates": [lon, lat]}
    where coordinates are already WGS84.

    Fallback: if the dict has "rdx"/"rdy" keys (RD New), convert via rd_to_wgs84.
    """
    if geometrie is None:
        return None, None

    geo_type = geometrie.get("type", "")
    coords = geometrie.get("coordinates")

    if geo_type == "Point" and coords and len(coords) >= 2:
        lon, lat = float(coords[0]), float(coords[1])
        # WGS84 sanity check: Amsterdam is ~52.37°N, 4.90°E
        # RD New x values are ~100_000–200_000; WGS84 lon values are 3–7 for NL
        if abs(lon) > 90 or abs(lat) > 90:
            # Likely RD New — convert
            lat, lon = rd_to_wgs84(lon, lat)
        return lat, lon

    # Fallback: look for explicit rd coordinate keys
    rdx = geometrie.get("rdx") or geometrie.get("x")
    rdy = geometrie.get("rdy") or geometrie.get("y")
    if rdx is not None and rdy is not None:
        return rd_to_wgs84(float(rdx), float(rdy))

    return None, None


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

def download_all_pages() -> list[dict]:
    """Paginate through the Amsterdam stamgegevens REST API."""
    url: str | None = f"{BASE_URL}?_format=json&page_size={PAGE_SIZE}"
    results: list[dict] = []
    while url:
        r = get_with_retry(url)
        data = r.json()
        embedded = data.get("_embedded", {})
        page_rows = embedded.get("stamgegevens", [])
        results.extend(page_rows)
        next_link = data.get("_links", {}).get("next", {})
        url = next_link.get("href") if isinstance(next_link, dict) else None
    return results


# ---------------------------------------------------------------------------
# Transform
# ---------------------------------------------------------------------------

def transform(rows: list[dict]) -> pa.Table:
    tree_ids: list[str | None] = []
    cities: list[str] = []
    species_list: list[str | None] = []
    tree_names: list[str | None] = []
    plant_dates: list[date | None] = []
    latitudes: list[float | None] = []
    longitudes: list[float | None] = []
    dbhs: list[float | None] = []

    for rec in rows:
        # tree_id
        raw_id = rec.get("id")
        tree_ids.append(f"ams-{raw_id}" if raw_id is not None else None)

        cities.append("NLAMS")

        # species: soortnaamLatijn (scientific name)
        species_list.append(normalize_species(rec.get("soortnaamLatijn")))

        # tree_name: soortnaamNederlands (Dutch common name)
        nl_name = rec.get("soortnaamNederlands")
        tree_names.append(nl_name.strip() if nl_name and nl_name.strip() else None)

        # plant_date: plantjaar (integer year)
        plant_dates.append(parse_plant_date(rec.get("plantjaar")))

        # coordinates
        lat, lon = extract_coords(rec.get("geometrie"))
        latitudes.append(lat)
        longitudes.append(lon)

        # DBH: stamdiameterklasse → midpoint cm → inches
        dbhs.append(parse_dbh(rec.get("stamdiameterklasse")))

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


# ---------------------------------------------------------------------------
# Validate
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    rows = download_all_pages()
    table = transform(rows)
    table = validate_coordinates(table, city="Amsterdam", city_code="NLAMS")
    emit(table)
