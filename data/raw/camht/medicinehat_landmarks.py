#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Medicine Hat's historic resources, from the City's own map (no licence
stated; displayed with attribution only, not licensed for reuse).

Source: "Historic Resources" (`Historic_Map_Tour_layer/FeatureServer/0`) on
the City of Medicine Hat's ArcGIS Online organisation (item
80ffcd9064b84cb493169d307b673c23, owner MHArcGISOnline), the layer behind the
City's "Historic Resources" story map: 80 points on 2026-11-21, each a
designated historic place in the city -- Municipal and Provincial Historic
Resources (the Canadian Bank of Commerce, the Courthouse, St. Patrick's
Church), National Historic Sites (Medalta Potteries, Medicine Hat Clay
Industries), the First Street South Municipal Historic Area and the Saamis
archaeological site -- with a description whose first line is usually the
street address.

It is the City's own list, so it comes first in the landmark source order;
the Alberta Register of Historic Places publishes no open geodata (the
province's open "Listing of Historic Resources" is a land-title table of
quarter sections, not places).  The item is not on opendata.medicinehat.ca,
whose items link the City's open GIS-data licence; the item states no
licence and no access information (checked 2026-11-21), so the source is
displayed with attribution only.

**The id is the name** (`mht-canadian-bank-of-commerce`): the layer is a
story-map tour with a dense 1..80 `F__OBJECTID` and nothing else that
identifies a place, and a tour stop is re-positioned more readily than it is
renamed.  The names are distinct; a repeat raises.  HTML entities in the
names (`&amp;`, `&nbsp;`) are decoded first.
"""

import html
import re
import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_point, iter_features
from shared.ingest import emit, make_point_wkt

LAYER = FeatureLayer(
    "https://services3.arcgis.com/2gQ2Dv4Fb016qFVU/arcgis/rest/services/"
    "Historic_Map_Tour_layer/FeatureServer/0"
)
OUT_FIELDS = "F__OBJECTID,name,description"

# A first description line is an address when it starts with a house number
# and is short; otherwise the description opens with prose.
ADDRESS_MAX_LEN = 80


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(html.unescape(str(value)).split())
    return text or None


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def address_of(description) -> str | None:
    if not description:
        return None
    first = html.unescape(str(description)).replace("\r", "\n").split("\n", 1)[0]
    first = " ".join(first.split()).rstrip(".")
    if not first or not first[0].isdigit() or len(first) > ADDRESS_MAX_LEN:
        return None
    return first


def transform(features: list[dict]) -> pa.Table:
    landmark_id: list[str] = []
    name: list[str] = []
    geometry_raw: list[str] = []
    address: list[str | None] = []
    seen: set[str] = set()

    for feature in features:
        rec = feature.get("attributes") or {}
        label = clean(rec.get("name"))
        if label is None:
            continue
        lat, lon = esri_point(feature.get("geometry"))
        if lat is None or lon is None:
            continue
        key = f"mht-{slug(label)}"
        if key in seen:
            raise RuntimeError(f"Medicine Hat landmarks: repeated name {label!r}")
        seen.add(key)
        landmark_id.append(key)
        name.append(label)
        geometry_raw.append(make_point_wkt(lon, lat))
        address.append(address_of(rec.get("description")))

    return pa.table(
        {
            "landmark_id": pa.array(landmark_id, type=pa.string()),
            "city": pa.array(["CAMHT"] * len(landmark_id), type=pa.string()),
            "name": pa.array(name, type=pa.string()),
            "geometry_raw": pa.array(geometry_raw, type=pa.string()),
            "address": pa.array(address, type=pa.string()),
        }
    )


if __name__ == "__main__":
    features: list[dict] = []
    for page in iter_features(
        LAYER,
        out_fields=OUT_FIELDS,
        return_geometry=True,
        out_sr=4326,
        order_by="F__OBJECTID",
    ):
        features.extend(page)
    emit(transform(features))
