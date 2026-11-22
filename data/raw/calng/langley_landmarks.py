#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Langley Township's historic places and heritage conservation areas.

Two official sources, both read live:

1. **The Township's inventory of historic places**: "LangleysHistoricPlaces_
   HFL_InventoriedSites view" (item 838d297431b747128230a452f038b503, "Public
   View version"), owned by the Township's heritage GIS account and behind its
   "Langley's Historic Places" story map: 122 points, each a Historic Site,
   Heritage Character Site, Historic Landscape Feature or Historic Cemetery
   (or one *recommended* as such), with its address and construction date.
   Licence: the item states none and it is not in the "Township of Langley
   Open Data" group, so the Township's Open Government License does not
   visibly cover it (no licence stated; displayed with attribution only, not
   licensed for reuse).
2. **The Province's Heritage Conservation Areas of BC**
   (`WHSE_HUMAN_CULTURAL_ECONOMIC.HIST_HERITAGE_CNSRV_AREAS_SV` on
   openmaps.gov.bc.ca, Open Government Licence - British Columbia): the
   Township's two designated areas, Fort Langley (1993) and Murrayville
   (2010).  The Township publishes the same two polygons itself, without a
   licence or a designation date.

Neither the Canadian Register of Historic Places (open.canada.ca: only the
federal designations, Fort Langley National Historic Site and its buildings,
with addresses and no coordinates) nor the BC Register of Historic Places
("Access Only" in the BC Data Catalogue) is an open, placed register.

**Ids**: the inventory's `OBJECTID` (`langley-hp-{oid}`): the layer is edited
in place (122 rows over OBJECTIDs 1..124, gaps where entries were deleted),
and it has no other key -- names repeat ("BARN", two "HALL RESIDENCE"s).
The areas take the Province's `HERITAGE_CNSRV_AREA_ID` (`langley-hca-{id}`).

Names are published as the inventory spells them (upper case).  The trailing
whitespace in `Inventory` ("Historic Site  ") is collapsed.
"""

import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_point, iter_features
from shared.platforms.wfs import WfsLayer, geojson_to_wkt, iter_wfs_features
from shared.ingest import emit

INVENTORY = FeatureLayer(
    "https://services5.arcgis.com/frpHL0Fv8koQRVWY/arcgis/rest/services/"
    "LangleysHistoricPlaces_HFL_InventoriedSites_view/FeatureServer/0",
    timeout=120,
)
INVENTORY_FIELDS = "OBJECTID,Site,Address,Construction_Date,Inventory"

HCA = WfsLayer(
    "https://openmaps.gov.bc.ca/geo/pub/wfs",
    "pub:WHSE_HUMAN_CULTURAL_ECONOMIC.HIST_HERITAGE_CNSRV_AREAS_SV",
    timeout=120,
)
# The register spells the municipality "Langley Township", once with a
# trailing CRLF.
HCA_FILTER = (
    "LOCAL_GOVERNMENT_NAME LIKE 'Langley Township%' AND DESIGNATION_ACTIVE_IND = 'Y'"
)


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def first_year(value) -> int | None:
    """The first four-digit year in a free-text date ("circa 1912", "pre-1890")."""
    digits = ""
    for ch in str(value or ""):
        if ch.isdigit():
            digits += ch
            if len(digits) == 4:
                year = int(digits)
                return year if 1700 <= year <= 2100 else None
        else:
            digits = ""
    return None


def inventory_rows(features: list[dict]) -> list[dict]:
    rows = []
    for feature in features:
        rec = feature.get("attributes") or {}
        name = clean(rec.get("Site"))
        lat, lon = esri_point(feature.get("geometry"))
        if name is None or lat is None or lon is None:
            continue
        rows.append(
            {
                "landmark_id": f"langley-hp-{int(rec['OBJECTID'])}",
                "name": name,
                "geometry_raw": f"POINT({lon} {lat})",
                "address": clean(rec.get("Address")),
                "property_type": clean(rec.get("Inventory")),
                "year_built": first_year(rec.get("Construction_Date")),
                "year_designated": None,
            }
        )
    return rows


def hca_rows(features: list[dict]) -> list[dict]:
    rows = []
    for feature in features:
        props = feature.get("properties") or {}
        area_id = clean(props.get("HERITAGE_CNSRV_AREA_ID"))
        name = clean(props.get("DESIGNATED_AREA_NAME"))
        wkt = geojson_to_wkt(feature.get("geometry"))
        if area_id is None or name is None or wkt is None:
            continue
        rows.append(
            {
                "landmark_id": f"langley-hca-{area_id}",
                "name": name,
                "geometry_raw": wkt,
                "address": None,
                "property_type": "Heritage Conservation Area",
                "year_built": None,
                "year_designated": first_year(props.get("DESIGNATED_DATE")),
            }
        )
    return rows


def to_table(rows: list[dict]) -> pa.Table:
    n = len(rows)
    col = lambda key: [r[key] for r in rows]  # noqa: E731
    return pa.table(
        {
            "landmark_id": pa.array(col("landmark_id"), type=pa.string()),
            "city": pa.array(["CALNG"] * n, type=pa.string()),
            "name": pa.array(col("name"), type=pa.string()),
            "geometry_raw": pa.array(col("geometry_raw"), type=pa.string()),
            "address": pa.array(col("address"), type=pa.string()),
            "property_type": pa.array(col("property_type"), type=pa.string()),
            "year_built": pa.array(col("year_built"), type=pa.int64()),
            "year_designated": pa.array(col("year_designated"), type=pa.int64()),
        }
    )


if __name__ == "__main__":
    inventory: list[dict] = []
    for page in iter_features(
        INVENTORY, out_fields=INVENTORY_FIELDS, return_geometry=True, out_sr=4326
    ):
        inventory.extend(page)
    areas: list[dict] = []
    for page in iter_wfs_features(
        HCA,
        sort_by="HIST_HCA_SV_SYSID",
        properties=[
            "HIST_HCA_SV_SYSID",
            "HERITAGE_CNSRV_AREA_ID",
            "DESIGNATED_AREA_NAME",
            "DESIGNATED_DATE",
            "SHAPE",
        ],
        cql_filter=HCA_FILTER,
    ):
        areas.extend(page)
    rows = inventory_rows(inventory) + hca_rows(areas)
    table = to_table(rows)
    ids = table["landmark_id"].to_pylist()
    if len(set(ids)) != len(ids):
        raise RuntimeError("Langley Township landmarks: repeated landmark_id")
    print(
        f"Langley Township landmarks: {table.num_rows} row(s) -- "
        f"{len(inventory)} inventoried place(s), {len(areas)} conservation area(s)",
        file=sys.stderr,
    )
    if table.num_rows == 0:
        raise RuntimeError("Langley Township landmarks produced no rows")
    emit(table)
