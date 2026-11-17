#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Hamilton's heritage register.

Source: "Heritage Properties" (item 8a9013bfc00f4502b02a404da1d1e1f9) on the
City's ArcGIS Online organisation, published on Open Hamilton under the City
of Hamilton Open Data Licence (named in the item's own licence field:
https://www.hamilton.ca/city-initiatives/strategies-actions/open-data-licence-terms-and-conditions).
It is the City's Municipal Heritage Register and inventory in one layer,
10,271 points, one per property.

**Only the register is published: `HERITAGE_STATUS` Designated (1,036 --
designated by by-law under Part IV or V of the Ontario Heritage Act) and
Registered Non-Designated (2,272 -- listed on the register under s.27).**
`Inventoried` (6,962) is the City's survey of buildings of possible interest,
not a designation, and is dropped -- Kingston, Montreal and Quebec City took
the statutory register in the same position.

**A registered property is not always named.**  `NAME` is filled for about
half of the register ("Whitehern", "Dundurn Castle"); the rest are houses
known by their address, which is the fallback (`STREET_NO_1 STREET_NAME,
COMMUNITY`).  A row with neither is skipped.

**`DATE_HERITAGE` is the construction year** (1788 ... 1970s; `0` and blanks
are unknown), so it is `year_built`.  The register carries no designation
date.  `property_type` is the register status, with the Heritage Conservation
District named for a Part V property.

**The id is `OBJECTID`** (`ham-hp-{objectid}`): the layer has no GlobalID and
no register number of its own.  The OBJECTID re-keys if the City reloads the
layer rather than editing it; landmarks are rebuilt whole, so that costs only
churn in the ids.
"""

import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_geometry_to_wkt, max_record_count
from shared.ingest import emit, get_json_with_retry

LAYER = FeatureLayer(
    "https://services.arcgis.com/rYz782eMbySr2srL/arcgis/rest/services/"
    "Heritage_Properties/FeatureServer/0",
    timeout=180,
)
OUT_FIELDS = (
    "OBJECTID,HERITAGE_STATUS,NAME,STREET_NO_1,STREET_NAME,COMMUNITY,"
    "DATE_HERITAGE,PART_IV,PART_V,HCD_NAME"
)
REGISTER_STATUSES = ("Designated", "Registered Non-Designated")
WHERE = "HERITAGE_STATUS IN (" + ", ".join(f"'{s}'" for s in REGISTER_STATUSES) + ")"

# The organisation's per-minute query quota answers with a 429 envelope that
# asks for a 60 s wait; back off long enough to outlast it.
MAX_RETRIES = 7
BACKOFF = 4.0


def iter_pages():
    size = max_record_count(LAYER)
    offset = 0
    while True:
        payload = get_json_with_retry(
            LAYER.query_url,
            params={
                "where": WHERE,
                "outFields": OUT_FIELDS,
                "returnGeometry": "true",
                "outSR": "4326",
                "orderByFields": "OBJECTID",
                "resultOffset": str(offset),
                "resultRecordCount": str(size),
                "f": "json",
            },
            timeout=LAYER.timeout,
            max_retries=MAX_RETRIES,
            backoff=BACKOFF,
        )
        features = payload.get("features") or []
        if not features:
            return
        yield features
        more = payload.get("exceededTransferLimit")
        if more is False or (more is None and len(features) < size):
            return
        offset += len(features)


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def parse_year(value) -> int | None:
    text = clean(value)
    if text is None or not text[:4].isdigit():
        return None
    year = int(text[:4])
    return year if 1500 <= year <= 2100 else None


def address_of(rec: dict) -> str | None:
    street = " ".join(
        part for part in (clean(rec.get("STREET_NO_1")), clean(rec.get("STREET_NAME"))) if part
    )
    if not street:
        return None
    community = clean(rec.get("COMMUNITY"))
    return f"{street}, {community}" if community else street


def property_type_of(rec: dict) -> str | None:
    status = clean(rec.get("HERITAGE_STATUS"))
    if status == "Designated":
        parts = []
        if clean(rec.get("PART_IV")) == "Yes":
            parts.append("Part IV")
        if clean(rec.get("PART_V")) == "Yes":
            district = clean(rec.get("HCD_NAME"))
            parts.append(f"Part V ({district})" if district else "Part V")
        if parts:
            return f"Designated, {' and '.join(parts)}"
    return status


def transform(features: list[dict]) -> pa.Table:
    landmark_id: list[str] = []
    name: list[str] = []
    geometry_raw: list[str] = []
    address: list[str | None] = []
    year_built: list[int | None] = []
    property_type: list[str | None] = []

    for feature in features:
        rec = feature.get("attributes") or {}
        street = address_of(rec)
        label = clean(rec.get("NAME")) or street
        if label is None:
            continue
        wkt = esri_geometry_to_wkt(feature.get("geometry"))
        if wkt is None or rec.get("OBJECTID") is None:
            continue
        landmark_id.append(f"ham-hp-{rec['OBJECTID']}")
        name.append(label)
        geometry_raw.append(wkt)
        address.append(street)
        year_built.append(parse_year(rec.get("DATE_HERITAGE")))
        property_type.append(property_type_of(rec))

    n = len(landmark_id)
    return pa.table(
        {
            "landmark_id": pa.array(landmark_id, type=pa.string()),
            "city": pa.array(["CAHAM"] * n, type=pa.string()),
            "name": pa.array(name, type=pa.string()),
            "geometry_raw": pa.array(geometry_raw, type=pa.string()),
            "address": pa.array(address, type=pa.string()),
            "year_built": pa.array(year_built, type=pa.int64()),
            "property_type": pa.array(property_type, type=pa.string()),
        }
    )


if __name__ == "__main__":
    features: list[dict] = []
    for page in iter_pages():
        features.extend(page)
    table = transform(features)
    print(
        f"Hamilton landmarks: {table.num_rows:,} of {len(features):,} register rows",
        file=sys.stderr,
    )
    if table.num_rows == 0:
        raise RuntimeError("Hamilton heritage register produced no landmarks")
    emit(table)
