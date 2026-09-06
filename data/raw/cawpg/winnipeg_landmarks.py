#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Winnipeg's list of Historical Resources.

Source: "Historical Resources" (`ptpx-kgiu`) on data.winnipeg.ca -- 728
buildings on the city's Commemorative List and Buildings Conservation List,
carrying the historical name, street address, construction date and grade of
each.

First preference in the runbook's landmark source order: an official
designation register on the same portal as the trees, read directly, with the
dataset's own `rowsUpdatedAt` as the probe.

**The id is Socrata's `:id`.**  The register publishes no key: `map_url` looks
like one (it carries the city's own record serial in `rsn=`) but is shared by
9 of the 728 rows, and `historical_name` repeats too.  `:id` is distinct on
every row and stable across a republish -- see `edmonton_landmarks.py`, which
made the same call for the same reason.
"""

import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import emit, make_point_wkt
from _socrata_shared import SYSTEM_ID, SocrataDataset, iter_rows, point_lon_lat

DATASET = SocrataDataset("data.winnipeg.ca", "ptpx-kgiu")

SELECT = (
    f"{SYSTEM_ID},historical_name,street_number,street_name,construction_date,"
    "grade,sub_code,point"
)


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def parse_year(value) -> int | None:
    """The leading four-digit year of a construction-date string, or None."""
    if value in (None, ""):
        return None
    digits = ""
    for char in str(value):
        if char.isdigit():
            digits += char
            if len(digits) == 4:
                return int(digits)
        elif digits:
            digits = ""
    return None


def transform(rows: list[dict]) -> pa.Table:
    landmark_id: list[str] = []
    city: list[str] = []
    name: list[str] = []
    geometry_raw: list[str | None] = []
    address: list[str | None] = []
    year_built: list[int | None] = []
    property_type: list[str | None] = []
    category: list[str | None] = []

    for rec in rows:
        raw_id = clean(rec.get(SYSTEM_ID))
        if raw_id is None:
            continue
        lon, lat = point_lon_lat(rec.get("point"))
        wkt = make_point_wkt(lon, lat)
        if wkt is None:
            continue
        street = clean(
            " ".join(
                part
                for part in (rec.get("street_number"), rec.get("street_name"))
                if part
            )
        )
        label = clean(rec.get("historical_name")) or street
        if label is None:
            continue

        landmark_id.append(f"wpg-{raw_id}")
        city.append("CAWPG")
        name.append(label)
        geometry_raw.append(wkt)
        address.append(street)
        year_built.append(parse_year(rec.get("construction_date")))
        # `grade` is the conservation grade (I, II, III); `sub_code` is which
        # of the two lists the building is on.
        property_type.append(clean(rec.get("grade")))
        category.append(clean(rec.get("sub_code")))

    return pa.table(
        {
            "landmark_id": pa.array(landmark_id, type=pa.string()),
            "city": pa.array(city, type=pa.string()),
            "name": pa.array(name, type=pa.string()),
            "geometry_raw": pa.array(geometry_raw, type=pa.string()),
            "address": pa.array(address, type=pa.string()),
            "year_built": pa.array(year_built, type=pa.int64()),
            "property_type": pa.array(property_type, type=pa.string()),
            "category": pa.array(category, type=pa.string()),
        }
    )


if __name__ == "__main__":
    rows: list[dict] = []
    for page in iter_rows(DATASET, select=SELECT):
        rows.extend(page)
    emit(transform(rows))
