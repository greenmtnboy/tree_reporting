#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Launceston's places on the Tasmanian Heritage Register.

Source: the "Tasmanian Heritage Register" layer of Heritage Tasmania's
`HT/HT_Public` MapServer on theLIST (services.thelist.tas.gov.au), the
spatial representation of the register the Tasmanian Heritage Council keeps
under the Historic Cultural Heritage Act 1995.  The layer's copyright text:
"Heritage Tasmania - Creative Commons (BY) [CC BY 3.0 AU] - the LIST (c)
State of Tasmania".  The City of Launceston publishes no heritage schedule
of its own on its open data hub.

The layer is statewide, one polygon per row, with a `MUNICIPALITY` column;
the city is `MUNICIPALITY = 'Launceston City Council'` (960 rows, 825
places, on 2026-11-21).  A place registered over several parcels
(`THR_ID` repeated, up to 11 rows) becomes one landmark whose geometry is
the MULTIPOLYGON of its parcels.  Both registration statuses (Permanently
and Provisionally Registered) are kept.

Mapped: `landmark_id` = `lst-{THR_ID}`; `name` = `THR_NAME` as written
(many are generic: "House", "Commercial Retail"); `register_number` =
`THR_ID`; `lm_type` = "Tasmanian Heritage Register"; `address` = `LOC_ADD`
in capitalised words; `geometry_raw` = the parcels as WKT, read in WGS84
(`outSR=4326`).
"""

import string
import sys
from collections import defaultdict
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_geometry_to_wkt, iter_features
from shared.ingest import emit

LAYER = FeatureLayer(
    "https://services.thelist.tas.gov.au/arcgis/rest/services/HT/HT_Public/MapServer/0",
    timeout=180,
)
FIELDS = "OBJECTID,THR_ID,THR_NAME,LOC_ADD"
WHERE = "MUNICIPALITY = 'Launceston City Council'"

CITY_CODE = "AULST"
ID_PREFIX = "lst"
REGISTER_NAME = "Tasmanian Heritage Register"


def clean(value) -> str | None:
    text = " ".join(str(value or "").split())
    return text or None


def display_address(value) -> str | None:
    """`34 HILL ST, WEST LAUNCESTON, 7250` -> `34 Hill St, West Launceston, 7250`."""
    text = clean(value)
    if text is None:
        return None
    return string.capwords(text.lower(), " ")


def build() -> pa.Table:
    places: dict[str, dict] = {}
    rings: dict[str, list] = defaultdict(list)
    for page in iter_features(
        LAYER,
        out_fields=FIELDS,
        where=WHERE,
        return_geometry=True,
        out_sr=4326,
        order_by="OBJECTID",
    ):
        for feature in page:
            attrs = feature.get("attributes") or {}
            number = clean(attrs.get("THR_ID"))
            name = clean(attrs.get("THR_NAME"))
            if not number or not name:
                continue
            places.setdefault(
                number, {"name": name, "address": display_address(attrs.get("LOC_ADD"))}
            )
            rings[number].extend((feature.get("geometry") or {}).get("rings") or [])

    numbers = sorted(n for n in places if rings[n])
    if not numbers:
        raise RuntimeError("No Tasmanian Heritage Register places for Launceston")
    geometry = [esri_geometry_to_wkt({"rings": rings[n]}) for n in numbers]
    return pa.table(
        {
            "landmark_id": pa.array([f"{ID_PREFIX}-{n}" for n in numbers], type=pa.string()),
            "city": pa.array([CITY_CODE] * len(numbers), type=pa.string()),
            "name": pa.array([places[n]["name"] for n in numbers], type=pa.string()),
            "geometry_raw": pa.array(geometry, type=pa.string()),
            "lm_type": pa.array([REGISTER_NAME] * len(numbers), type=pa.string()),
            "register_number": pa.array(numbers, type=pa.string()),
            "address": pa.array([places[n]["address"] for n in numbers], type=pa.string()),
        }
    )


if __name__ == "__main__":
    table = build()
    print(f"{CITY_CODE} landmarks: {table.num_rows} heritage places", file=sys.stderr)
    emit(table)
