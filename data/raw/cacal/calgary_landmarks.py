#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Calgary's Inventory of Evaluated Historic Resources.

Source: "Historic Resource" (`99yf-6c5u`) on data.calgary.ca -- the Calgary
Heritage Authority's register, 870 resources it "has evaluated and formally
acknowledged to have significant heritage value", carrying the name, address,
community, construction year and resource type of each.

This is the first preference in the runbook's landmark source order: an
official designation registry, on the same portal as the trees.  So Calgary
needs neither the Nominatim geocoding path nor a committed CSV, and Overpass is
not involved, so there is nothing to stage -- the register is small, the
refresh reads it directly, and the probe is the dataset's own `rowsUpdatedAt`.
"""

import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import emit, make_point_wkt
from _socrata_shared import SocrataDataset, iter_rows, point_lon_lat

DATASET = SocrataDataset("data.calgary.ca", "99yf-6c5u")

SELECT = (
    "id,name,resource_alternate_nm,resource_ty,address,community,"
    "construction_yr,point"
)


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def parse_year(value) -> int | None:
    """The leading four-digit year of a construction-year string, or None.

    The register writes ranges and qualifiers into this field ("1912-14",
    "c. 1905"), so take the first year mentioned rather than requiring the
    whole value to parse as a number.
    """
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
    neighborhood: list[str | None] = []
    year_built: list[int | None] = []
    property_type: list[str | None] = []

    for rec in rows:
        raw_id = clean(rec.get("id"))
        if raw_id is None:
            continue
        lon, lat = point_lon_lat(rec.get("point"))
        wkt = make_point_wkt(lon, lat)
        if wkt is None:
            continue
        street = clean(rec.get("address"))
        # A handful of rows carry no name at all; the street address is the
        # only thing that identifies them, and an unnamed pin is worse than an
        # addressed one.
        label = (
            clean(rec.get("name"))
            or clean(rec.get("resource_alternate_nm"))
            or street
        )
        if label is None:
            continue

        landmark_id.append(f"cal-{raw_id}")
        city.append("CACAL")
        name.append(label)
        geometry_raw.append(wkt)
        address.append(street)
        neighborhood.append(clean(rec.get("community")))
        year_built.append(parse_year(rec.get("construction_yr")))
        property_type.append(clean(rec.get("resource_ty")))

    return pa.table(
        {
            "landmark_id": pa.array(landmark_id, type=pa.string()),
            "city": pa.array(city, type=pa.string()),
            "name": pa.array(name, type=pa.string()),
            "geometry_raw": pa.array(geometry_raw, type=pa.string()),
            "address": pa.array(address, type=pa.string()),
            "neighborhood": pa.array(neighborhood, type=pa.string()),
            "year_built": pa.array(year_built, type=pa.int64()),
            "property_type": pa.array(property_type, type=pa.string()),
        }
    )


if __name__ == "__main__":
    rows: list[dict] = []
    for page in iter_rows(DATASET, select=SELECT):
        rows.extend(page)
    emit(transform(rows))
