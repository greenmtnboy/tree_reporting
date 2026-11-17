#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Fingal's Record of Protected Structures.

Source: "Development Plan 2023 2029 RPS Record of Protected Structures_FCC"
(item 601387c1d5ed4f6aa172fcf231a61fd3, owner `FingalAdmin`), the register
of protected structures in the adopted Fingal Development Plan 2023-2029,
one point per structure -- the runbook's first preference, an official
designation register, on the same ArcGIS organisation as the trees and
under the same licence (credits: "Contains Irish Public Sector Data
licensed under a Creative Commons Attribution 4.0 International (CC BY 4.0)
licence"; licence field links https://creativecommons.org/licenses/by/4.0/).
790 points on 2026-11-16, all inside the county; eight are on Lambay and
Rockabill, east of the trees.  (`Record_of_Protected_Structures_RPS_2023_2029`
is an unlicensed earlier copy of the same 790 rows.)

**Keyed on the RPS number** (`2`, `747a`), unique and never null over the
layer: `fcc-rps-747a`.  It also publishes as `register_number`, and
`lm_type` names the register, South Dublin's and Ballarat's convention.

**The name** is `Structure_Name` (`Knocknagin House`).  A name the register
repeats (`Milestone` 17 times, `Thatched Cottage`, `Martello Tower`) gets
its address appended so the rows can be told apart; the 81 structures with
no name (mostly terraced houses) are named by their address
(`Sandpits Cottages, Luttrellstown Road, Castleknock`).  The county suffix
(`Co. Dublin`, `Dublin 15`) is dropped from an address used in a name.
"""

import re
import sys
from collections import Counter
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit
from shared.platforms.arcgis import FeatureLayer, esri_point, iter_features

LAYER = FeatureLayer(
    "https://services5.arcgis.com/CI1e5PKQXvJgmJK8/arcgis/rest/services/"
    "FCC_Development_Plan_2023_2029_RPS_Record_of_Protected_Structures/FeatureServer/0",
    timeout=120,
)
OUT_FIELDS = "OBJECTID,RPS_Number,Structure_Name,Street_Number,Street_Address"
REGISTER = "Record of Protected Structures"

# `, Co. Dublin` / `, Dublin 15` at the end of an address.
COUNTY_SUFFIX = re.compile(r"\s*,\s*(co\.?\s*dublin|dublin\s*\d*)\s*$", re.IGNORECASE)


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def address(attrs: dict) -> str | None:
    street = clean(attrs.get("Street_Address"))
    if street is None:
        return None
    street = COUNTY_SUFFIX.sub("", street)
    street = re.sub(r"\s*,\s*", ", ", street).strip(", ")
    number = clean(attrs.get("Street_Number"))
    return f"{number} {street}" if number else street or None


def read_register() -> list[dict]:
    features = [
        f
        for page in iter_features(
            LAYER, out_fields=OUT_FIELDS, return_geometry=True, order_by="OBJECTID"
        )
        for f in page
    ]
    keys = Counter(clean(f["attributes"].get("RPS_Number")) for f in features)
    names = Counter(clean(f["attributes"].get("Structure_Name")) for f in features)
    records: list[dict] = []
    unusable = repeated = 0
    for feature in features:
        attrs = feature["attributes"]
        key = clean(attrs.get("RPS_Number"))
        own = clean(attrs.get("Structure_Name"))
        where = address(attrs)
        if own and names[own] > 1 and where:
            name = f"{own}, {where}"
        else:
            name = own or where
        lat, lon = esri_point(feature.get("geometry"))
        if key is None or name is None or lat is None or lon is None:
            unusable += 1
            continue
        if keys[key] > 1:
            repeated += 1
            continue
        records.append(
            {
                "landmark_id": f"fcc-rps-{key.lower()}",
                "name": name,
                "geometry_raw": f"POINT ({lon} {lat})",
                "register_number": key,
            }
        )
    print(
        f"Fingal protected structures: {len(records)} kept of {len(features)}; "
        f"{unusable} dropped with no number, name or point, {repeated} carrying a "
        "number another row also carries",
        file=sys.stderr,
    )
    return records


def transform(records: list[dict]) -> pa.Table:
    n = len(records)
    return pa.table(
        {
            "landmark_id": pa.array([r["landmark_id"] for r in records], type=pa.string()),
            "city": pa.array(["IEFIN"] * n, type=pa.string()),
            "name": pa.array([r["name"] for r in records], type=pa.string()),
            "geometry_raw": pa.array([r["geometry_raw"] for r in records], type=pa.string()),
            "lm_type": pa.array([REGISTER] * n, type=pa.string()),
            "register_number": pa.array([r["register_number"] for r in records], type=pa.string()),
        }
    )


if __name__ == "__main__":
    emit(transform(read_register()))
