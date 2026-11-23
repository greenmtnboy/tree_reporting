#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Port St. Lucie, FL's parks and civic recreation places, from the City.

**No heritage register exists to read.**  The City (incorporated 1961)
publishes no local historic register on its ArcGIS Online or its open data
hub, and the National Park Service's National Register of Historic Places
(`cultural_resources/nrhp_locations`) has no listing in Port St. Lucie on
2026-11-22: the three inside `CITY_BOUNDS["USPSL"]` are in White City and
Stuart.  So, as Fort St. John and Mississauga did, this takes the City's own
register of named places, the runbook's "same portal as the trees":

  * **"PARKS"** (ArcGIS item 65f2da9f968e463c8cf240ca452177f7, owner
    `pslgisAdmin`, `PARKS/FeatureServer/0`, shared to the City's "Open Data
    Share" group): 54 points on 2026-11-22, one per City park or recreation
    facility -- Oak Hammock Park, the Botanical Gardens, Sandhill Crane Park,
    the US Submarine Veterans Memorial, the MIDFLORIDA Event Center, the
    Saints Golf Course -- each with its street address.

Licence: none stated (no licence stated; displayed with attribution only,
not licensed for reuse).  The item's `licenseInfo` and `accessInformation`
are empty and the open data hub states no terms.

**Ids** are `psl-park-{OBJECTID}`: the layer has no GlobalID and its
`PARK_ID` is the park's name, which a rename would change.  `OBJECTID` runs
1..60 with gaps (18, 36, 43, 48-50), a layer edited in place rather than
reloaded, so it is stable.  A repeat raises.  `PSL TRANSIT FACILITY` (a bus
depot) is not a landmark and is skipped, as is a point outside the city's box
(McCarty Ranch Preserve, a City preserve in the county 10 km west).  Names
are published in capitals and are title-cased here; `address` is `ADDRESS`.
"""

import re
import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import CITY_BOUNDS, emit
from shared.platforms.arcgis import FeatureLayer, esri_geometry_to_wkt, iter_features

PARKS = FeatureLayer(
    "https://services1.arcgis.com/YdUP5V6WwzeG8T8r/arcgis/rest/services/PARKS/FeatureServer/0",
    timeout=120,
)
OUT_FIELDS = "OBJECTID,PARK_ID,ADDRESS"

# Rows in the layer that are not a place anyone would call a landmark.
NOT_LANDMARKS = frozenset({"psl transit facility"})

# Words kept as written when an all-caps name is title-cased.
KEEP_WORDS = {
    "psl": "PSL",
    "us": "US",
    "midflorida": "MIDFLORIDA",
    "o.l.": "O.L.",
    "sr": "Sr.",
    "@": "at",
    "mcchesney": "McChesney",
    # address quadrants
    "ne": "NE",
    "nw": "NW",
    "se": "SE",
    "sw": "SW",
}


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).replace("’", "'").split())
    return text or None


def title(name: str) -> str:
    if not name.isupper():
        return name
    words = []
    for word in name.lower().split():
        if word in KEEP_WORDS:
            words.append(KEEP_WORDS[word])
        else:
            words.append("/".join(p[:1].upper() + p[1:] for p in word.split("/")))
    out = " ".join(words)
    # "Ian T Zook", "Charles E Ray": a lone initial takes a full stop.
    out = re.sub(r"(?<= )([A-Z])(?= )", r"\1.", out)
    return re.sub(r"\bSt\b(?!\.)", "St.", out)


def build() -> pa.Table:
    lat_min, lat_max, lon_min, lon_max = CITY_BOUNDS["USPSL"]
    landmark_id: list[str] = []
    name: list[str] = []
    geometry_raw: list[str] = []
    address: list[str | None] = []
    seen: set[str] = set()
    skipped = 0

    for page in iter_features(
        PARKS, out_fields=OUT_FIELDS, return_geometry=True, out_sr=4326, order_by="OBJECTID"
    ):
        for feature in page:
            rec = feature.get("attributes") or {}
            oid, label = rec.get("OBJECTID"), clean(rec.get("PARK_ID"))
            geom = feature.get("geometry") or {}
            wkt = esri_geometry_to_wkt(geom)
            if oid is None or label is None or wkt is None or label.lower() in NOT_LANDMARKS:
                skipped += 1
                continue
            if not (lat_min <= geom.get("y", 0) <= lat_max and lon_min <= geom.get("x", 0) <= lon_max):
                skipped += 1
                continue
            lid = f"psl-park-{oid}"
            if lid in seen:
                raise RuntimeError(f"Port St. Lucie landmarks: repeated id {lid}")
            seen.add(lid)
            landmark_id.append(lid)
            name.append(title(label))
            geometry_raw.append(wkt)
            addr = clean(rec.get("ADDRESS"))
            address.append(title(addr) if addr else None)

    n = len(landmark_id)
    print(f"Port St. Lucie landmarks: {n} place(s), {skipped} skipped", file=sys.stderr)
    if n == 0:
        raise RuntimeError("Port St. Lucie PARKS layer produced no landmarks")
    return pa.table(
        {
            "landmark_id": pa.array(landmark_id, type=pa.string()),
            "city": pa.array(["USPSL"] * n, type=pa.string()),
            "name": pa.array(name, type=pa.string()),
            "geometry_raw": pa.array(geometry_raw, type=pa.string()),
            "address": pa.array(address, type=pa.string()),
        }
    )


if __name__ == "__main__":
    emit(build())
