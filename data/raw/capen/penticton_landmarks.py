#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Penticton's Community Heritage Register, from the City of Penticton.

Source: the `Heritage Site` layer (`External_PRD/Planning/MapServer/235`) on
the City's own ArcGIS Server: 52 parcels on 2026-11-22, 46 of them recognised
as "Community Heritage Register" and 2 as "Heritage Designation", each with
its build date and a link to its page in the City's Heritage Registry PDF.
The rest (`Mitchell Block`, `Capitol Theatre`, `Post Office`, `KVR Right of
Way`) carry no recognition fields but are in the Registry PDF all the same,
and are kept.  The register columns (`RCGNTN_TYE`, `CMMN_ST_NM`,
`CUR_FC_CAT`) are the Canadian Register of Historic Places' own schema.

Licence: none stated for this layer (no licence stated; displayed with
attribution only, not licensed for reuse).  The City's Open Government
Licence covers what its Open Penticton hub catalogues, and the hub does not
catalogue this layer; the hosted copy (`Planning_PRD/FeatureServer/235`) has
no item of its own and its service item states no licence.  Neither the BC
Register of Historic Places ("Access Only" in the BC Data Catalogue) nor the
Canadian Register (federal designations only on open.canada.ca) is an open,
placed alternative.

**Ids**: the server's `GlobalID` (`pen-hs-{guid}`), unique and non-null over
the 52 rows; the layer is edited in place (`OBJECTID` 1..60 with gaps).
`HE_SITE_ID` is null on the four unrecognised rows.

`name` is the City's `STRUCTURE` label ("Herrick/Debeck House", "KVR
Station"), present on every row; the register's `CMMN_ST_NM` is not used, as
it is an address on some rows ("810 Hudson Street"), truncated on others
("Elite Caf"), and missing on the four unrecognised ones.  `address` is `ADDRESS` ("No
address" is none); `year_built` is `DATE`; `year_designated` the year of the
recognition date (`RCGNTN_STE`, `20060501000000`); `property_type` the
register's current function (`CUR_FC_CAT`, "Residence").
"""

import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_geometry_to_wkt, iter_features
from shared.ingest import emit

LAYER = FeatureLayer(
    "https://maps.penticton.ca/utility/rest/services/External_PRD/Planning/MapServer/235",
    timeout=120,
)
OUT_FIELDS = "OBJECTID,GlobalID,STRUCTURE,CMMN_ST_NM,ADDRESS,DATE,RCGNTN_STE,CUR_FC_CAT"
NO_ADDRESS = frozenset({"no address", "n/a"})


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).replace("’", "'").split())
    return text or None


def name_of(rec: dict) -> str | None:
    return clean(rec.get("STRUCTURE")) or clean(rec.get("CMMN_ST_NM"))


def year_of(value) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    if len(text) >= 4 and text[:4].isdigit():
        year = int(text[:4])
        return year if 1700 <= year <= 2100 else None
    return None


def build() -> pa.Table:
    rows: list[dict] = []
    for page in iter_features(
        LAYER, out_fields=OUT_FIELDS, return_geometry=True, out_sr=4326
    ):
        for feature in page:
            rec = feature.get("attributes") or {}
            guid = clean(rec.get("GlobalID"))
            name = name_of(rec)
            wkt = esri_geometry_to_wkt(feature.get("geometry"))
            if guid is None or name is None or wkt is None:
                continue
            address = clean(rec.get("ADDRESS"))
            if address and address.lower() in NO_ADDRESS:
                address = None
            rows.append(
                {
                    "landmark_id": f"pen-hs-{guid.strip('{}').lower()}",
                    "name": name,
                    "geometry_raw": wkt,
                    "address": address,
                    "year_built": year_of(rec.get("DATE")),
                    "year_designated": year_of(rec.get("RCGNTN_STE")),
                    "property_type": clean(rec.get("CUR_FC_CAT")),
                }
            )

    n = len(rows)
    col = lambda key: [r[key] for r in rows]  # noqa: E731
    table = pa.table(
        {
            "landmark_id": pa.array(col("landmark_id"), type=pa.string()),
            "city": pa.array(["CAPEN"] * n, type=pa.string()),
            "name": pa.array(col("name"), type=pa.string()),
            "geometry_raw": pa.array(col("geometry_raw"), type=pa.string()),
            "address": pa.array(col("address"), type=pa.string()),
            "year_built": pa.array(col("year_built"), type=pa.int64()),
            "year_designated": pa.array(col("year_designated"), type=pa.int64()),
            "property_type": pa.array(col("property_type"), type=pa.string()),
        }
    )
    ids = table["landmark_id"].to_pylist()
    if len(set(ids)) != len(ids):
        raise RuntimeError("Penticton landmarks: repeated landmark_id")
    if n == 0:
        raise RuntimeError("Penticton landmarks produced no rows")
    print(f"Penticton landmarks: {n} heritage site(s)", file=sys.stderr)
    return table


if __name__ == "__main__":
    emit(build())
