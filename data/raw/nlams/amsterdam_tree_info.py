#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///

"""
Fetch Amsterdam tree data from the Amsterdam REST API and emit Arrow IPC to stdout.

Source: https://api.data.amsterdam.nl/v1/bomen/stamgegevens/
API:    https://api.data.amsterdam.nl/v1/bomen/stamgegevens/?_format=json&page_size=2000

Field mapping (API v2 field names as of 2025):
  id                  -> tree_id  (prefixed "ams-" for global uniqueness)
  soortnaam           -> species  (scientific name only, normalised)
  soortnaamTop        -> tree_name (Dutch common name with genus, e.g. "Linde (Tilia)")
  jaarVanAanleg       -> plant_date (year integer → January 1 of that year)
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
from collections.abc import Iterator

import pyarrow as pa
from datetime import date
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import (
    emit,
    enforce_tree_schema,
    get_json_with_retry,
    iter_link_pages,
    normalize_species,
    parse_plant_date_year,
    rd_to_wgs84,
    stream_to_table,
    validate_coordinates,
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

def iter_pages() -> Iterator[list[dict]]:
    """Yield one page of stamgegevens records at a time.

    A generator rather than a list because Amsterdam is the largest ingest in
    the repo: holding all ~325k records as dicts peaked at 882 MB of Python
    heap (tracemalloc, so excluding interpreter overhead and pyarrow's own
    buffers).  That is fine on a workstation and evidently not fine in a cloud
    executor, where this ingest failed every time it actually rebuilt while
    passing locally.  The caller converts each page to Arrow and drops the
    dicts, so peak memory is one page plus the accumulated columnar data.
    """
    return iter_link_pages(
        f"{BASE_URL}?_format=json&page_size={PAGE_SIZE}", rows_key="stamgegevens"
    )


def download_all_pages() -> list[dict]:
    """Every record, as one list.  Kept for callers that want it all in memory
    (the dedup/analysis scripts); the ingest itself streams via `iter_pages`."""
    return [row for page in iter_pages() for row in page]


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

        # species: soortnaam (scientific name; was "soortnaamLatijn" in API v1)
        species_list.append(normalize_species(
            rec.get("soortnaam") or rec.get("soortnaamLatijn")
        ))

        # tree_name: soortnaamTop (Dutch common name; was "soortnaamNederlands" in v1)
        nl_name = rec.get("soortnaamTop") or rec.get("soortnaamNederlands")
        tree_names.append(nl_name.strip() if nl_name and nl_name.strip() else None)

        # plant_date: jaarVanAanleg (year; was "plantjaar" in API v1)
        plant_dates.append(parse_plant_date(
            rec.get("jaarVanAanleg") or rec.get("plantjaar")
        ))

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
    # Drop stumps, not unidentified trees.
    #
    # This used to filter on a null `species`, which conflated two very
    # different records.  Of the 21,592 rows it removed, ~17,400 were real
    # trees the inventory simply has no species for (`typeObject` "Boom …",
    # "Vormboom", "Fruitboom", "Knotboom") — every other city keeps those and
    # `enforce_tree_schema` gives them the UNKNOWN_SPECIES sentinel, so
    # dropping them made Amsterdam under-report by ~6% for no reason.
    #
    # The other ~4,160 were `Stobbe` — tree stumps — which are not trees and
    # should not be on the map whether or not the inventory recorded what they
    # used to be.  `_ingest_shared._SPECIES_PLACEHOLDERS` already treats
    # "stump" as a non-taxon; filtering on the record type applies the same
    # rule, and catches the far larger group the null test never saw: 25,454
    # records are Stobbe in total, so ~21,300 stumps *with* a species were
    # being published as living trees.  Net effect is -3,872 for Amsterdam.
    table = stream_to_table(
        iter_pages(),
        transform,
        keep=lambda r: (r.get("typeObject") or "").strip() != "Stobbe",
        label="Amsterdam ingest",
    )
    table = validate_coordinates(table, city="Amsterdam", city_code="NLAMS")
    table = enforce_tree_schema(table, city="Amsterdam", data_source="AMSTERDAM_OPENDATA")
    emit(table)
