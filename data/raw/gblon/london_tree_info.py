#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///

"""
Fetch London Public Realm Trees from the London Datastore and emit Arrow IPC
to stdout.

Source: https://data.london.gov.uk/dataset/2r45m  (London Public Realm Trees)
Package ID: 2r45m  (CKAN)
~1.14 million public realm trees from all 32 London boroughs, City of London,
TfL, Royal Parks, LLDC, and Quintain. Last updated November 2025.

CSV columns (2025 release):
  uniqueid        -> tree_id  (prefixed "lon-")
  taxon_species   -> species  (scientific/taxonomic name; may be genus-only)
  common_name     -> tree_name
  lat / lon       -> latitude / longitude
  girth_dbh       -> diameter_at_breast_height  ("32 cm" string → DBH inches)
  (no plant_date column in this dataset)

The download URL is resolved dynamically from the CKAN package so it continues
to work when the dataset is refreshed with a new filename.
"""

import csv
import io
import math
import sys
from datetime import date
from pathlib import Path

import pyarrow as pa
import requests
sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import (
    emit,
    normalize_species,
    validate_coordinates,
    circumference_cm_to_dbh_inches,
)

PACKAGE_URL = "https://data.london.gov.uk/api/3/action/package_show?id=2r45m"

# Hardcoded fallback in case the CKAN package API is unavailable
FALLBACK_URL = (
    "https://data.london.gov.uk/download/2r45m"
    "/e62a6a1f-390d-4193-ae32-3aabd9846f36/Borough_tree_list_2025Nov.csv"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def parse_plant_date(val: str | None) -> date | None:
    """Parse a planting year (integer string) or ISO date string to a date."""
    if not val or not val.strip():
        return None
    v = val.strip()
    # Try ISO date first (YYYY-MM-DD)
    if len(v) >= 10 and v[4] == "-":
        try:
            return date.fromisoformat(v[:10])
        except ValueError:
            pass
    # Try 4-digit year
    try:
        year = int(v[:4])
        if 1700 <= year <= 2100:
            return date(year, 1, 1)
    except ValueError:
        pass
    return None


def parse_float(val: str | None) -> float | None:
    if not val or not val.strip():
        return None
    # Strip unit suffixes like " cm", " m", " in" (2025 dataset uses "32 cm" format)
    v = val.strip().split()[0].replace(",", "")
    try:
        return float(v)
    except ValueError:
        return None


def osgb36_to_wgs84(easting: float, northing: float) -> tuple[float, float]:
    """
    Approximate conversion from OSGB36 British National Grid (EPSG:27700)
    to WGS84 lat/lon (EPSG:4326).

    Uses pyproj when available for accuracy; falls back to a lightweight
    polynomial approximation sufficient for tree-level precision (~3 m).
    """
    try:
        from pyproj import Transformer
        transformer = Transformer.from_crs("EPSG:27700", "EPSG:4326", always_xy=True)
        lon, lat = transformer.transform(easting, northing)
        return lat, lon
    except ImportError:
        pass

    # Helmert-style polynomial approximation (OS formula, accuracy ~3 m)
    # Source: https://www.ordnancesurvey.co.uk/documents/resources/guide-coordinate-systems-great-britain.pdf
    a, b = 6377563.396, 6356256.909      # Airy 1830 ellipsoid
    F0 = 0.9996012717
    lat0, lon0 = math.radians(49.0), math.radians(-2.0)
    N0, E0 = -100000.0, 400000.0

    n = (a - b) / (a + b)
    e2 = 1 - (b / a) ** 2
    e = math.sqrt(e2)
    N = northing
    E = easting

    lat_p = lat0
    for _ in range(10):
        M = (
            b * F0 * (
                (1 + n + 5 / 4 * n**2 + 5 / 4 * n**3) * (lat_p - lat0)
                - (3 * n + 3 * n**2 + 21 / 8 * n**3) * math.sin(lat_p - lat0) * math.cos(lat_p + lat0)
                + (15 / 8 * n**2 + 15 / 8 * n**3) * math.sin(2 * (lat_p - lat0)) * math.cos(2 * (lat_p + lat0))
                - 35 / 24 * n**3 * math.sin(3 * (lat_p - lat0)) * math.cos(3 * (lat_p + lat0))
            )
        )
        lat_p = lat_p + (N - N0 - M) / (a * F0)

    sin_lat = math.sin(lat_p)
    cos_lat = math.cos(lat_p)
    tan_lat = math.tan(lat_p)

    nu = a * F0 / math.sqrt(1 - e2 * sin_lat**2)
    rho = a * F0 * (1 - e2) / (1 - e2 * sin_lat**2) ** 1.5
    eta2 = nu / rho - 1

    VII = tan_lat / (2 * rho * nu)
    VIII = tan_lat / (24 * rho * nu**3) * (5 + 3 * tan_lat**2 + eta2 - 9 * tan_lat**2 * eta2)
    IX = tan_lat / (720 * rho * nu**5) * (61 + 90 * tan_lat**2 + 45 * tan_lat**4)
    X = 1 / (cos_lat * nu)
    XI = 1 / (cos_lat * 6 * nu**3) * (nu / rho + 2 * tan_lat**2)
    XII = 1 / (cos_lat * 120 * nu**5) * (5 + 28 * tan_lat**2 + 24 * tan_lat**4)
    XIIA = 1 / (cos_lat * 5040 * nu**7) * (61 + 662 * tan_lat**2 + 1320 * tan_lat**4 + 720 * tan_lat**6)

    dE = E - E0
    lat_out = lat_p - VII * dE**2 + VIII * dE**4 - IX * dE**6
    lon_out = lon0 + X * dE - XI * dE**3 + XII * dE**5 - XIIA * dE**7

    return math.degrees(lat_out), math.degrees(lon_out)


def detect_delimiter(header_line: str) -> str:
    if header_line.count(";") > header_line.count(","):
        return ";"
    return ","


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

def resolve_csv_url() -> str:
    """Resolve the current CSV download URL from the CKAN package metadata."""
    try:
        r = requests.get(PACKAGE_URL, timeout=30)
        r.raise_for_status()
        resources = r.json().get("result", {}).get("resources", [])
        csv_resources = [
            res for res in resources
            if (res.get("format") or "").upper() == "CSV"
        ]
        if csv_resources:
            # Pick the most recently modified CSV
            csv_resources.sort(
                key=lambda x: x.get("last_modified") or x.get("created") or "",
                reverse=True,
            )
            return csv_resources[0]["url"]
    except Exception:
        pass
    return FALLBACK_URL


def download_csv() -> list[dict]:
    """Resolve and download the current CSV from the London Datastore."""
    url = resolve_csv_url()
    r = requests.get(url, stream=True, timeout=600)
    r.raise_for_status()
    buf = io.BytesIO()
    for chunk in r.iter_content(chunk_size=1024 * 1024):
        if chunk:
            buf.write(chunk)
    raw_bytes = buf.getvalue()
    try:
        text = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw_bytes.decode("latin-1")
    first_line = text.split("\n")[0]
    delimiter = detect_delimiter(first_line)
    rows = list(csv.DictReader(io.StringIO(text), delimiter=delimiter))
    if not rows:
        raise RuntimeError(f"London tree CSV produced 0 rows (url={url})")
    return rows


# ---------------------------------------------------------------------------
# Transform
# ---------------------------------------------------------------------------

def transform(rows: list[dict]) -> pa.Table:
    if not rows:
        raise ValueError("London CSV produced 0 rows")

    # Build lowercase → actual key map for robust column lookup
    sample = rows[0]
    field_map = {k.lower().strip(): k for k in sample.keys()}

    def col(*candidates: str) -> str | None:
        for c in candidates:
            if c in field_map:
                return field_map[c]
        return None

    # --- Column detection ---
    id_field = col(
        "uniqueid", "tree_id", "id", "objectid", "gis_id", "treeref",
        "reference_number", "ref", "tree_ref",
    )
    species_field = col(
        "taxon_species",
        "full_scientific_name", "latin_name", "botanical_name",
        "scientific_name", "species_latin", "latin", "species",
    )
    common_field = col(
        "common_name", "common", "english_name", "common_name_uk",
    )
    year_field = col(
        "year_planted", "planting_year", "planted_year",
        "date_planted", "plant_date", "planted",
    )
    # Coordinate fields — prefer lat/lon directly; fall back to easting/northing
    lat_field = col("lat", "latitude", "y", "northing_wgs84", "lat_wgs84")
    lon_field = col("lon", "longitude", "lng", "x", "easting_wgs84", "lon_wgs84")
    easting_field = col("easting", "easting_m", "e")
    northing_field = col("northing", "northing_m", "n")

    # DBH / girth detection (order matters — prefer already-converted fields)
    # girth_dbh is the 2025 dataset field; values are like "32 cm" (string with unit)
    dbh_in_field = col("dbh_in", "diameter_inches", "dbh_inches")
    dbh_cm_field = col("diameter_breast_height_cm", "dbh_cm", "dbh")
    girth_cm_field = col(
        "girth_dbh",
        "girth_at_breast_height_cm", "girth_cm", "girth",
        "circumference_cm", "circumference_at_breast_height_cm",
        "trunk_circumference_cm",
    )

    tree_ids: list[str | None] = []
    cities: list[str] = []
    species_list: list[str | None] = []
    tree_names: list[str | None] = []
    plant_dates: list[date | None] = []
    latitudes: list[float | None] = []
    longitudes: list[float | None] = []
    dbhs: list[float | None] = []

    for row in rows:
        # tree_id
        raw_id = row.get(id_field, "") if id_field else ""
        tree_ids.append(f"lon-{raw_id.strip()}" if raw_id and raw_id.strip() else None)
        cities.append("GBLON")

        # species (scientific name only)
        raw_species = row.get(species_field, "") if species_field else ""
        species_list.append(normalize_species(raw_species))

        # common name / tree name
        raw_common = row.get(common_field, "") if common_field else ""
        tree_names.append(raw_common.strip() if raw_common and raw_common.strip() else None)

        # plant_date
        raw_year = row.get(year_field, "") if year_field else ""
        plant_dates.append(parse_plant_date(raw_year))

        # latitude / longitude
        raw_lat = row.get(lat_field, "") if lat_field else ""
        raw_lon = row.get(lon_field, "") if lon_field else ""
        lat_val = parse_float(raw_lat)
        lon_val = parse_float(raw_lon)

        if lat_val is None and easting_field and northing_field:
            raw_e = row.get(easting_field, "")
            raw_n = row.get(northing_field, "")
            e_val = parse_float(raw_e)
            n_val = parse_float(raw_n)
            if e_val is not None and n_val is not None:
                try:
                    lat_val, lon_val = osgb36_to_wgs84(e_val, n_val)
                except Exception:
                    lat_val, lon_val = None, None

        latitudes.append(lat_val)
        longitudes.append(lon_val)

        # DBH in inches
        if dbh_in_field:
            dbhs.append(parse_float(row.get(dbh_in_field, "")))
        elif dbh_cm_field:
            v = parse_float(row.get(dbh_cm_field, ""))
            dbhs.append(v / 2.54 if v is not None else None)
        elif girth_cm_field:
            v = parse_float(row.get(girth_cm_field, ""))
            dbhs.append(circumference_cm_to_dbh_inches(v))
        else:
            dbhs.append(None)

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
# Validate & emit
# ---------------------------------------------------------------------------

def validate(table: pa.Table) -> None:
    validate_coordinates(table, city="London")


if __name__ == "__main__":
    rows = download_csv()
    table = transform(rows)
    validate(table)
    emit(table)
