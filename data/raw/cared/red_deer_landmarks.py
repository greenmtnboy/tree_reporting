#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Red Deer's historical sites, from the City's Zoning Bylaw register (no
licence stated; displayed with attribution only, not licensed for reuse).

Source: `CoRD_View_Layers/LUB_HistoricalSite_approved` on the City of Red
Deer's ArcGIS Enterprise server -- the historical sites the Zoning Bylaw
(3357/2024) maps, 105 polygons (2026-11-21), every one `approved`, under
`HistoricalJurisd = 'City of Red Deer'`.  Two designations:

  * `HP-n`, "Historical Preservation building or site" (20): the City's
    designated Municipal Historic Resources.
  * `HS-n`, "Historically Significant building or site" (85): listed for
    their significance, including nine ceremonial and landmark trees
    (`HistoricalSiteType = 'tree'`).

It is the City's own designation register, so it is the first preference in
the landmark source order: read directly, nothing geocoded or staged.  The
layer is not on data.reddeer.ca, whose Open Government Licence covers that
site's datasets; the layer, its service and the City's ArcGIS Online items
state no licence (checked 2026-11-21).

**The id is the register number `HistoricalNum`** (`HS-75` ->
`red-hs-75`): distinct and non-null on all 105 rows, and the number the
bylaw itself cites, so it survives a reload of the layer.  `property_type`
is the designation with the kind of resource (`Historical Preservation
building or site (building)`).  The register has no address and no
construction year.
"""

import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_geometry_to_wkt, max_record_count
from shared.ingest import emit, get_json_with_retry

LAYER = FeatureLayer(
    "https://arcgis.reddeer.ca/arcgis/rest/services/CoRD_View_Layers/"
    "LUB_HistoricalSite_approved/FeatureServer/0",
    timeout=120,
)
OUT_FIELDS = "OBJECTID,HistoricalStatus,HistoricalNum,HistoricalType_Rule,HistoricalSiteType,HistoricalSiteName"
WHERE = "HistoricalStatus = 'approved'"


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
                "geometryPrecision": "7",
                "orderByFields": "OBJECTID",
                "resultOffset": str(offset),
                "resultRecordCount": str(size),
                "f": "json",
            },
            timeout=LAYER.timeout,
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


def property_type_of(rec: dict) -> str | None:
    designation = clean(rec.get("HistoricalType_Rule"))
    kind = clean(rec.get("HistoricalSiteType"))
    if designation and kind:
        return f"{designation} ({kind})"
    return designation or kind


def transform(features: list[dict]) -> pa.Table:
    landmark_id: list[str] = []
    name: list[str] = []
    geometry_raw: list[str] = []
    property_type: list[str | None] = []
    seen: set[str] = set()

    for feature in features:
        rec = feature.get("attributes") or {}
        number = clean(rec.get("HistoricalNum"))
        label = clean(rec.get("HistoricalSiteName"))
        wkt = esri_geometry_to_wkt(feature.get("geometry"))
        if number is None or label is None or wkt is None:
            continue
        key = f"red-{number.lower().replace(' ', '')}"
        if key in seen:
            raise RuntimeError(f"Red Deer historical register repeats {number}")
        seen.add(key)
        landmark_id.append(key)
        name.append(label)
        geometry_raw.append(wkt)
        property_type.append(property_type_of(rec))

    n = len(landmark_id)
    return pa.table(
        {
            "landmark_id": pa.array(landmark_id, type=pa.string()),
            "city": pa.array(["CARED"] * n, type=pa.string()),
            "name": pa.array(name, type=pa.string()),
            "geometry_raw": pa.array(geometry_raw, type=pa.string()),
            "property_type": pa.array(property_type, type=pa.string()),
        }
    )


if __name__ == "__main__":
    features: list[dict] = []
    for page in iter_pages():
        features.extend(page)
    table = transform(features)
    print(
        f"Red Deer landmarks: {table.num_rows:,} of {len(features):,} register rows",
        file=sys.stderr,
    )
    if table.num_rows == 0:
        raise RuntimeError("Red Deer historical register produced no landmarks")
    emit(table)
