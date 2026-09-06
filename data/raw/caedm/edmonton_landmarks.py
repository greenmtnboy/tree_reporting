#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Edmonton's Register and Inventory of Historic Resources.

Source: "The Register and Inventory of Historic Resources in Edmonton"
(`jgsn-dhai`) on data.edmonton.ca -- 1,104 resources with the name, address,
neighbourhood, construction year and designation type of each.

First preference in the runbook's landmark source order: an official
designation registry on the same portal as the trees, read directly, with the
dataset's own `rowsUpdatedAt` as the probe.

**The id is Socrata's `:id`.**  The register publishes no key of its own --
not a register number, not a file number -- and neither the name nor the
address is unique across the 1,104 rows.  `:id` is the system row identifier:
always present, distinct on every row here, and stable across a republish,
which is more than a name-and-address hash would be (that churns the moment
somebody fixes a typo).  It is the same reasoning `_socrata_shared` gives for
ordering pages by it.
"""

import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import emit, make_point_wkt
from _socrata_shared import SYSTEM_ID, SocrataDataset, iter_rows

DATASET = SocrataDataset("data.edmonton.ca", "jgsn-dhai")

SELECT = (
    f"{SYSTEM_ID},name_of_historic_resource,building_address,neighbourhood,"
    "construction_completion_year,warning_type_1,latitude,longitude"
)


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def parse_year(value) -> int | None:
    """The leading four-digit year of a construction-year string, or None."""
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


def as_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
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
        raw_id = clean(rec.get(SYSTEM_ID))
        if raw_id is None:
            continue
        wkt = make_point_wkt(as_float(rec.get("longitude")), as_float(rec.get("latitude")))
        if wkt is None:
            continue
        street = clean(rec.get("building_address"))
        label = clean(rec.get("name_of_historic_resource")) or street
        if label is None:
            continue

        landmark_id.append(f"edm-{raw_id}")
        city.append("CAEDM")
        name.append(label)
        geometry_raw.append(wkt)
        address.append(street)
        neighborhood.append(clean(rec.get("neighbourhood")))
        year_built.append(parse_year(rec.get("construction_completion_year")))
        # "Municipal Historic Resource", "Provincial Historic Resource", ... --
        # the designation that put the building on the register.
        property_type.append(clean(rec.get("warning_type_1")))

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
