#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///

import sys
import requests
import pyarrow as pa
from datetime import date, timezone, datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import emit, enforce_tree_schema, normalize_species, validate_coordinates

BASE_URL = "https://gis.arboretum.harvard.edu/arcgis/rest/services/Maps/Explorer/MapServer/34/query"
PAGE_SIZE = 100_000
OUT_FIELDS = "OBJECTID,SCIENTIFIC_NAME,COMMON_NAME,LATITUDE,LONGITUDE,DBH_NUM,DBH_UNIT_CODE,PLANT_DT"

# Only include living, geographically mapped specimens
WHERE = "IS_DEAD=0 AND IS_MAPPED=1"


def strip_cultivar(name: str | None) -> str | None:
    """Strip cultivar notation (single-quoted suffix) from a scientific name.

    'Carpinus betulus \\'Columnaris\\'' → 'Carpinus betulus'
    'Tsuga canadensis' → 'Tsuga canadensis'
    """
    if not name or not name.strip():
        return None
    stripped = name.split("'")[0].strip()
    return stripped or None


def parse_plant_date(ms: int | None) -> date | None:
    """Convert ArcGIS millisecond timestamp to a Python date, or None."""
    if ms is None:
        return None
    try:
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).date()
    except (OSError, OverflowError, ValueError):
        return None


def convert_dbh(dbh_num: float | None, unit_code: str | None) -> float | None:
    """Return DBH in inches.  ArcGIS unit codes: 'in' = inches, 'cm' = centimeters."""
    if dbh_num is None:
        return None
    if unit_code and unit_code.lower() == "cm":
        return dbh_num / 2.54
    # Treat unknown / 'in' as already inches
    return float(dbh_num)


def fetch_page(offset: int) -> list[dict]:
    params = {
        "where": WHERE,
        "outFields": OUT_FIELDS,
        "resultOffset": offset,
        "resultRecordCount": PAGE_SIZE,
        "f": "json",
    }
    r = requests.get(BASE_URL, params=params, timeout=120)
    r.raise_for_status()
    data = r.json()
    return [f["attributes"] for f in data.get("features", [])]


def fetch_all() -> list[dict]:
    # Get total count first
    r = requests.get(
        BASE_URL,
        params={"where": WHERE, "returnCountOnly": "true", "f": "json"},
        timeout=30,
    )
    r.raise_for_status()
    total = r.json().get("count", 0)

    records: list[dict] = []
    for offset in range(0, total, PAGE_SIZE):
        records.extend(fetch_page(offset))
    return records


def build_table(records: list[dict]) -> pa.Table:
    tree_ids: list[str | None] = []
    cities: list[str] = []
    sources: list[str] = []
    species_list: list[str | None] = []
    tree_names: list[str | None] = []
    plant_dates: list[date | None] = []
    latitudes: list[float | None] = []
    longitudes: list[float | None] = []
    dbhs: list[float | None] = []

    for rec in records:
        obj_id = rec.get("OBJECTID")
        tree_ids.append(f"arb-{obj_id}" if obj_id is not None else None)
        cities.append("USBOS")
        sources.append("ARBORETUM")
        species_list.append(normalize_species(strip_cultivar(rec.get("SCIENTIFIC_NAME"))))
        raw_common = rec.get("COMMON_NAME")
        tree_names.append(raw_common.strip() if raw_common and raw_common.strip() else None)
        plant_dates.append(parse_plant_date(rec.get("PLANT_DT")))
        latitudes.append(float(rec["LATITUDE"]) if rec.get("LATITUDE") is not None else None)
        longitudes.append(float(rec["LONGITUDE"]) if rec.get("LONGITUDE") is not None else None)
        dbhs.append(convert_dbh(rec.get("DBH_NUM"), rec.get("DBH_UNIT_CODE")))

    return pa.table(
        {
            "tree_id": pa.array(tree_ids, type=pa.string()),
            "city": pa.array(cities, type=pa.string()),
            "usbos_source": pa.array(sources, type=pa.string()),
            "species": pa.array(species_list, type=pa.string()),
            "tree_name": pa.array(tree_names, type=pa.string()),
            "plant_date": pa.array(plant_dates, type=pa.date32()),
            "latitude": pa.array(latitudes, type=pa.float64()),
            "longitude": pa.array(longitudes, type=pa.float64()),
            "diameter_at_breast_height": pa.array(dbhs, type=pa.float64()),
        }
    )


if __name__ == "__main__":
    records = fetch_all()
    table = build_table(records)
    table = validate_coordinates(table, city="Arboretum", city_code="USBOS")
    table = enforce_tree_schema(table, city="Arboretum")
    emit(table)
