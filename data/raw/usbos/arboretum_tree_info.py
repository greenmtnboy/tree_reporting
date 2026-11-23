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
from shared.ingest import emit, enforce_tree_schema, normalize_species, validate_coordinates

BASE_URL = "https://gis.arboretum.harvard.edu/arcgis/rest/services/Maps/Explorer/MapServer/34/query"
PAGE_SIZE = 100_000
OUT_FIELDS = (
    "OBJECTID,SCIENTIFIC_NAME,LATITUDE,LONGITUDE,DBH_NUM,DBH_UNIT_CODE,DBH_UNIT,PLANT_DT"
)
DBH_UNIT_CODES = {"c": "cm", "cm": "cm", "i": "in", "in": "in", "m": "m"}
DBH_UNIT_LABELS = {
    "centimeter": "cm", "centimeters": "cm", "centimetre": "cm", "centimetres": "cm",
    "inch": "in", "inches": "in",
    "meter": "m", "meters": "m", "metre": "m", "metres": "m",
}

# Only include living, geographically mapped specimens
WHERE = "IS_DEAD=0 AND IS_MAPPED=1"


def parse_plant_date(ms: int | None) -> date | None:
    """Convert ArcGIS millisecond timestamp to a Python date, or None."""
    if ms is None:
        return None
    try:
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).date()
    except (OSError, OverflowError, ValueError):
        return None


def convert_dbh(
    dbh_num: float | None, unit_code: str | None, unit_label: str | None = None
) -> float | None:
    """Convert an explicitly identified Harvard measurement to inches.

    Harvard uses C, I and M. If either field is absent, a recognized value
    in the other is sufficient. Unknown or conflicting units stay null
    rather than being interpreted as inches.
    """
    if dbh_num is None:
        return None
    code = (unit_code or "").strip().lower()
    label = (unit_label or "").strip().lower()
    code_unit = DBH_UNIT_CODES.get(code)
    label_unit = DBH_UNIT_LABELS.get(label)
    if (code and code_unit is None) or (label and label_unit is None):
        return None
    if code_unit and label_unit and code_unit != label_unit:
        return None
    unit = code_unit or label_unit
    if unit == "cm":
        return dbh_num / 2.54
    if unit == "m":
        return dbh_num / 0.0254
    if unit == "in":
        return float(dbh_num)
    return None


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
    species_list: list[str | None] = []
    plant_dates: list[date | None] = []
    latitudes: list[float | None] = []
    longitudes: list[float | None] = []
    dbhs: list[float | None] = []

    for rec in records:
        obj_id = rec.get("OBJECTID")
        tree_ids.append(f"arb-{obj_id}" if obj_id is not None else None)
        cities.append("USBOS")
        # The quoted cultivar stays in: enforce_tree_schema reduces the
        # species to the taxon and keeps the selection on the tree row.
        species_list.append(normalize_species(rec.get("SCIENTIFIC_NAME")))
        plant_dates.append(parse_plant_date(rec.get("PLANT_DT")))
        latitudes.append(float(rec["LATITUDE"]) if rec.get("LATITUDE") is not None else None)
        longitudes.append(float(rec["LONGITUDE"]) if rec.get("LONGITUDE") is not None else None)
        dbhs.append(
            convert_dbh(rec.get("DBH_NUM"), rec.get("DBH_UNIT_CODE"), rec.get("DBH_UNIT"))
        )

    return pa.table(
        {
            "tree_id": pa.array(tree_ids, type=pa.string()),
            "city": pa.array(cities, type=pa.string()),
            "species": pa.array(species_list, type=pa.string()),
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
    table = enforce_tree_schema(table, city="Arboretum", data_source="ARNOLD_ARBORETUM")
    emit(table)
