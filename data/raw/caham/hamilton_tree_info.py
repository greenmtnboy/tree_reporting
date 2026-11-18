#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Hamilton's city-owned tree inventory, from Open Hamilton.

Source: "Public Tree Inventory" (item 08e50c6cbb2545b4897d613a817fa4d4, a
hosted view owned by the City's forestry GIS account), shared into the "Open
Hamilton Content" group that generates the open.hamilton.ca catalogue:
"Detailed information about individual city-owned trees within a defined
management area in Hamilton".  The item's own licence field is empty; the
catalogue's licence is the City of Hamilton Open Data Licence (v1.1,
https://www.hamilton.ca/city-initiatives/strategies-actions/open-data-licence-terms-and-conditions),
which the City's other Open Hamilton layers name explicitly.  Attribution
required: "Contains public sector Data made available under the City of
Hamilton's Open Data Licence".

**The layer has seven columns**: `TREE_ID`, `STATUS`, `WARD`,
`SPECIES_COMMON`, `SPECIES_SCIENTIFIC`, `DBH`, `GLOBALID`.  No planting date,
no crown, no height.  Geometry is stored in NAD83 / UTM 17N (EPSG:26917) and
there are no lat/lon attributes, so the geometry is requested reprojected to
WGS84 server-side (`outSR=4326`).

**Only `STATUS = 'Existing'` is a tree in the ground.**  The coded-value
domain is Existing / Stump / Proposed / In Review / Denied / Retired; measured
2026-11-17: Existing 281,540, Proposed 1,431, Denied 564, Stump 348, In Review
33, Retired 0.  Proposed / In Review / Denied are planting requests and plans,
Stump and Retired are removed trees; all are filtered server-side.

**The id is `GLOBALID`** (`ham-{guid}`).  `TREE_ID`, the forestry asset
number, is not a key: 367 rows have none and several values repeat (e.g.
4554227 twice).  `GLOBALID` is the layer's system-maintained GlobalID field,
non-null and unique on every row.  `enforce_tree_schema` still raises on a
repeat or a null.

**Species is `SPECIES_SCIENTIFIC`**, a coded-value domain whose names are
already binomials, with cultivars in quotes (`Acer x freemanii 'Autumn
Blaze'`, `Pyrus calleryana 'chanticleer'`) -- `enforce_tree_schema` splits
those into `cultivar`.  Whitespace is collapsed first: some domain values
carry a no-break space (`Magnolia\\xa0'Ann'`).  The domain has OCR-style typos
(`Thuja occldentalis`, 9.5k rows; `Robinia pseudoacacla`, `Juglans clnerea`,
`Larix lariclna`): those belong in the shared misspelling table, not here.
`SPECIES_COMMON` is never mapped -- about 2,500 Existing rows have a common
name but a blank scientific one, and they publish as Unknown.

**DBH is centimetres** (median 17, 5th percentile 3, 95th percentile 63 over
Existing rows; inches would make the median street tree 43 cm across).
Non-positive values are nulled here; keying errors above the schema's
200-inch ceiling (the layer's maximum is 4,556,269) are nulled there.
"""

import sys
import time
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_point, max_record_count
from shared.ingest import (
    cm_to_inches,
    emit,
    enforce_tree_schema,
    get_json_with_retry,
    UpstreamUnavailable,
    stream_to_table,
    validate_coordinates,
)

LAYER = FeatureLayer(
    "https://services.arcgis.com/rYz782eMbySr2srL/arcgis/rest/services/"
    "Public_Tree_Inventory/FeatureServer/0",
    timeout=300,
)
OUT_FIELDS = "OBJECTID,GLOBALID,SPECIES_SCIENTIFIC,DBH"
WHERE = "STATUS = 'Existing'"

# The City's ArcGIS Online organisation meters queries: 6,000 "request units"
# a minute, shared with every visitor to the City's own map apps.  An
# over-quota request is answered with a 429 envelope asking for a 60 s wait,
# and an unpaced read of the whole layer trips it about fifty pages in.  So
# the pages are paced (PAGE_INTERVAL_S between requests, ~12 minutes for the
# layer), and a 429 is waited out in full minutes, QUOTA_WAITS times, before
# the read gives up as unavailable.
PAGE_INTERVAL_S = 3.0
QUOTA_WAIT_S = 65.0
QUOTA_WAITS = 20


def query(params: dict) -> dict:
    """One page, waiting out the organisation's per-minute quota."""
    for attempt in range(QUOTA_WAITS + 1):
        try:
            return get_json_with_retry(
                LAYER.query_url, params=params, timeout=LAYER.timeout,
                max_retries=3,
            )
        except UpstreamUnavailable as exc:
            if "429" not in str(exc) or attempt == QUOTA_WAITS:
                raise
            print(
                f"Hamilton: over the portal's query quota, waiting {QUOTA_WAIT_S:.0f}s "
                f"({attempt + 1}/{QUOTA_WAITS})",
                file=sys.stderr,
            )
            time.sleep(QUOTA_WAIT_S)
    raise AssertionError("unreachable")


def iter_pages():
    """Pages of Esri features in OBJECTID order, WGS84 geometry.

    Keyset paging (`OBJECTID > last`) rather than `resultOffset`, so a page
    deep in the layer costs the portal no more than the first.  Ordered by the
    OID, a short or empty page is the end.
    """
    size = max_record_count(LAYER)
    last = -1
    pages = 0
    while True:
        if pages:
            time.sleep(PAGE_INTERVAL_S)
        payload = query(
            {
                "where": f"{WHERE} AND OBJECTID > {last}",
                "outFields": OUT_FIELDS,
                "returnGeometry": "true",
                "outSR": "4326",
                "geometryPrecision": "7",
                "orderByFields": "OBJECTID",
                "resultRecordCount": str(size),
                "f": "json",
            }
        )
        features = payload.get("features") or []
        if not features:
            return
        yield features
        pages += 1
        if pages % 25 == 0:
            print(f"Hamilton: {pages} pages read", file=sys.stderr)
        last = max(f["attributes"]["OBJECTID"] for f in features)
        if len(features) < size and not payload.get("exceededTransferLimit"):
            return


# Free text typed into the species field that is not a name at all (one row
# each, 2026-11): a condition and a location note.  The shared schema catches
# common names ("Copper beech") but would keep these as invented genera.
NOT_SPECIES = frozenset({"good", "inside the blvd."})


def clean(value) -> str | None:
    if value is None:
        return None
    # Some domain values carry a replacement character and a no-break space
    # where a space was meant (`Pterocarya�\xa0Fraxinifolia`).
    text = " ".join(str(value).replace("�", " ").split())
    return text or None


def scientific_name(value) -> str | None:
    text = clean(value)
    return None if text is None or text.lower() in NOT_SPECIES else text


def diameter_inches(value) -> float | None:
    if value is None:
        return None
    number = float(value)
    return cm_to_inches(number) if number > 0 else None


def transform(features: list[dict]) -> pa.Table:
    tree_id: list[str] = []
    species: list[str | None] = []
    latitude: list[float | None] = []
    longitude: list[float | None] = []
    dbh: list[float | None] = []

    for feature in features:
        rec = feature.get("attributes") or {}
        raw_id = str(rec.get("GLOBALID") or "").strip().strip("{}").lower()
        lat, lon = esri_point(feature.get("geometry"))
        tree_id.append(f"ham-{raw_id}" if raw_id else None)
        species.append(scientific_name(rec.get("SPECIES_SCIENTIFIC")))
        latitude.append(lat)
        longitude.append(lon)
        dbh.append(diameter_inches(rec.get("DBH")))

    n = len(tree_id)
    return pa.table(
        {
            "tree_id": pa.array(tree_id, type=pa.string()),
            "city": pa.array(["CAHAM"] * n, type=pa.string()),
            "species": pa.array(species, type=pa.string()),
            "plant_date": pa.array([None] * n, type=pa.date32()),
            "latitude": pa.array(latitude, type=pa.float64()),
            "longitude": pa.array(longitude, type=pa.float64()),
            "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
        }
    )


def has_id(feature: dict) -> bool:
    return bool(str((feature.get("attributes") or {}).get("GLOBALID") or "").strip())


if __name__ == "__main__":
    table = stream_to_table(
        iter_pages(), transform, keep=has_id, label="Hamilton OpenData"
    )
    table = validate_coordinates(table, city="Hamilton", city_code="CAHAM")
    table = enforce_tree_schema(
        table, city="Hamilton", data_source="HAMILTON_OPENDATA"
    )
    emit(table)
