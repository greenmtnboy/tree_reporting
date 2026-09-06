#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Toronto's Places of Interest and Attractions.

Source: "Places of Interest and Toronto Attractions" on
`ckan0.cf.opendata.inter.prod-toronto.ca`, resource `f131dbb9`, 178 places --
the CN Tower, Casa Loma, the Royal Ontario Museum, the Hockey Hall of Fame,
the Distillery Historic District, St. Lawrence Market -- each with a name, a
category and a point.  Read straight through the datastore: no Nominatim
geocoding, no committed CSV and no staging object, since only Overpass-backed
and hand-curated sources need those.

**This is not the runbook's first preference, and the choice is deliberate.**
Toronto does publish an official designation registry -- the Heritage Register,
12,332 properties, refreshed this month -- and it is passed over for two
reasons.  It is distributed as a shapefile and nothing else: no CSV, no
GeoJSON, no datastore, so reading it would mean a shapefile parser no other
source here needs.  And 12,332 individually listed properties are mostly
addresses of heritage row houses, which is a worse answer than 178 recognisable
places for what the landmark layer is actually for -- naming the ground so the
map and the agent have somewhere to stand.  Compare Calgary's register at 870.

If the Heritage Register ever gains a CSV or GeoJSON resource, it is the better
source and this should move to it.
"""

import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from _ckan_shared import CkanResource, iter_datastore_rows, point_lon_lat
from _ingest_shared import emit, make_point_wkt

RESOURCE = CkanResource(
    "ckan0.cf.opendata.inter.prod-toronto.ca",
    "f131dbb9-37b6-4e3e-9323-ff7f1c97395d",
)

FIELDS = "_id,NAME,CATEGORY,ADDRESS_FULL,WEBSITE,geometry"


def clean(value) -> str | None:
    """Collapse whitespace, and treat the portal's literal "None" as null.

    Toronto's CKAN ETL renders a missing value as the four characters `None`
    rather than an empty field -- see `toronto_tree_info.py`, where the same
    thing reaches the species column.
    """
    if value is None:
        return None
    text = " ".join(str(value).split())
    return None if text in ("", "None") else text


def transform(rows: list[dict]) -> pa.Table:
    landmark_id: list[str] = []
    city: list[str] = []
    name: list[str] = []
    geometry_raw: list[str | None] = []
    address: list[str | None] = []
    category: list[str | None] = []

    for rec in rows:
        label = clean(rec.get("NAME"))
        if label is None:
            continue
        lon, lat = point_lon_lat(rec.get("geometry"))
        wkt = make_point_wkt(lon, lat)
        if wkt is None:
            continue
        # `_id` is the datastore row number rather than a published asset id,
        # and this dataset has no other key -- the city publishes GEOID and
        # ADDRESS_POINT_ID, which identify the *address*, not the attraction,
        # and repeat where two attractions share a building.  A landmark id is
        # not joined to anything outside this table, so a row number is
        # tolerable here in a way it would not be for a tree_id.
        landmark_id.append(f"tor-{rec['_id']}")
        city.append("CATOR")
        name.append(label)
        geometry_raw.append(wkt)
        address.append(clean(rec.get("ADDRESS_FULL")))
        category.append(clean(rec.get("CATEGORY")))

    return pa.table(
        {
            "landmark_id": pa.array(landmark_id, type=pa.string()),
            "city": pa.array(city, type=pa.string()),
            "name": pa.array(name, type=pa.string()),
            "geometry_raw": pa.array(geometry_raw, type=pa.string()),
            "address": pa.array(address, type=pa.string()),
            "category": pa.array(category, type=pa.string()),
        }
    )


if __name__ == "__main__":
    rows: list[dict] = []
    for page in iter_datastore_rows(RESOURCE, fields=FIELDS):
        rows.extend(page)
    emit(transform(rows))
