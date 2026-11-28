#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Sarasota County, Florida's National Register listings.

**The National Register of Historic Places** (the National Park Service's
`cultural_resources/nrhp_locations` MapServer, points then polygons) with
`State = FLORIDA` and `County = Sarasota`: the inventory is the County's, so
the register is cut to the county, not to one city's name (the listings are
filed under Sarasota, Venice, North Port, Nokomis, Osprey, Laurel, Englewood
and Miakka).  The county attribute is the cut rather than `CITY_BOUNDS`,
whose box stops at the inventory's eastern edge (-82.2) while the county runs
to about -82.06.  Listed buildings, sites and structures, keyed
`sarasota-nr-{NRIS_Refnum}` with `CertDate` as `year_designated`; districts
are areas and are not published (Durham's rule).  NPS data is a US federal
work.

No local register is published openly: the County's GIS publishes its
historical *markers* (`Hosted/HistoricalMarker`, commemorative plaques, not
designations), and the City of Sarasota's "Historic District Overlays" item
states it "may not be reproduced, sold, or distributed".
"""

import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import esri_geometry_to_wkt, iter_features  # noqa: E402
from shared.ingest import emit  # noqa: E402
from shared.nrhp import COLUMNS, NATIONAL_TYPE, NRHP_FIELDS, NRHP_LAYERS, clean  # noqa: E402

CITY_CODE = "USSRQ"
PREFIX = "sarasota"
COUNTY_WHERE = "State = 'FLORIDA' AND County = 'Sarasota'"


def national_register() -> dict[str, list]:
    seen: set[str] = set()
    rows: dict[str, list] = {k: [] for k in COLUMNS}
    for layer in NRHP_LAYERS:
        for page in iter_features(
            layer, out_fields=NRHP_FIELDS, where=COUNTY_WHERE,
            return_geometry=True, out_sr=4326,
        ):
            for feature in page:
                attrs = feature.get("attributes") or {}
                ref = clean(attrs.get("NRIS_Refnum"))
                name = clean(attrs.get("RESNAME"))
                wkt = esri_geometry_to_wkt(feature.get("geometry"))
                if not ref or not name or wkt is None or ref in seen:
                    continue
                if clean(attrs.get("STATUS")) != "Listed":
                    continue
                if (clean(attrs.get("ResType")) or "").lower() == "district":
                    continue
                seen.add(ref)
                cert = clean(attrs.get("CertDate")) or ""
                rows["landmark_id"].append(f"{PREFIX}-nr-{ref}")
                rows["name"].append(name)
                rows["geometry_raw"].append(wkt)
                rows["address"].append(clean(attrs.get("Address")))
                rows["lm_type"].append(NATIONAL_TYPE)
                rows["register_number"].append(ref)
                rows["year_designated"].append(int(cert[:4]) if cert[:4].isdigit() else None)
    if not rows["landmark_id"]:
        raise RuntimeError("no National Register listings found in Sarasota County")
    return rows


def build() -> pa.Table:
    data = national_register()
    n = len(data["landmark_id"])
    return pa.table(
        {
            "landmark_id": pa.array(data["landmark_id"], type=pa.string()),
            "name": pa.array(data["name"], type=pa.string()),
            "city": pa.array([CITY_CODE] * n, type=pa.string()),
            "geometry_raw": pa.array(data["geometry_raw"], type=pa.string()),
            "address": pa.array(data["address"], type=pa.string()),
            "lm_type": pa.array(data["lm_type"], type=pa.string()),
            "register_number": pa.array(data["register_number"], type=pa.string()),
            "year_designated": pa.array(data["year_designated"], type=pa.int64()),
        }
    )


if __name__ == "__main__":
    emit(build())
