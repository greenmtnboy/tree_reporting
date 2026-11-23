#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Fort St. John's parks and named civic places, from the City's open data.

**No heritage register exists to read.**  The City publishes no community
heritage register; the Province's Historic Places layer
(`WHSE_HUMAN_CULTURAL_ECONOMIC.HIST_HISTORIC_ENVIRONMNT_PA_SV`, the BC
Register of Historic Places) holds no Fort St. John entry on 2026-11-22 and
is "Access Only" in the BC Data Catalogue; the Canadian Register of Historic
Places lists only Tse'K'wa National Historic Site, which lies at Charlie Lake
outside the municipal boundary.  So, as Mississauga did, this takes the
City's own registers of named places (the runbook's "same portal as the
trees"), both catalogued on data-fortstjohn.opendata.arcgis.com under the
Open Government Licence - Fort St. John v2.0
(https://data-fortstjohn.opendata.arcgis.com/pages/city-of-fort-st-john-open-government-data-license):

  * **"Recreation Areas"** (item 66b0dcbb2f9e4478b8f2751cb4742475): "all the
    parks within the City", 50 polygons of which 30 are named (Centennial
    Park, Kin Park, Fish Creek Community Forest, Fort St. John Cemetery, ...).
    An unnamed polygon names no ground and is skipped.  Id
    `fsj-park-{FACILITYID}`.
  * **"Points of Interest"** (item cce6e547a4984239be24093a0a095717): "City
    facilities, parks and other notable locations", 183 points, most of them
    businesses, gas stations, clinics, playgrounds and ball diamonds.
    `PLACE_TYPES` keeps the types that name a public place (cemeteries,
    civic and institutional buildings, theatres, schools, churches, the
    hospital, the airport) and the recreation facilities that are destinations
    in their own right; an amenity inside a park (`Kin Park Diamond 4`,
    `Playground`) is not a landmark.  A point whose name is a park's name
    (`Kin Park`, `Festival Plaza`) is that park and is skipped, and so is a point outside the City's
    envelope (the Rod & Gun Club at Charlie Lake).  Id
    `fsj-poi-{FACILITYID}`.

`FACILITYID` is unique and non-null over both layers (2026-11-22); a row
without one is skipped, a repeat raises.  `property_type` is `Park` for the
recreation areas and the point's `POINTTYPE` otherwise.  Park names are
published in capitals and are title-cased here.
"""

import re
import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_geometry_to_wkt, iter_features
from shared.ingest import emit

BASE = "https://services.arcgis.com/uFSCv839a7S1MRb7/arcgis/rest/services"
PARKS = FeatureLayer(f"{BASE}/Recreation_Areas/FeatureServer/0", timeout=120)
POI = FeatureLayer(f"{BASE}/Points_of_Interest/FeatureServer/0", timeout=120)

# POINTTYPE values that name a public place.  Left out: Business, Financial,
# Food & Drink, Gas Station, Grocery Store, Accomadation [sic], Health Care
# (clinics, pharmacies, dentists), Infrastructure, Sanitary Facilities,
# Community Support, PreSchool, Recreations (one tennis court).
PLACE_TYPES = frozenset({
    "Cemetery",
    "Educational",
    "Emergency Services",
    "Government",
    "Information",
    "Institutional",
    "Municipal",
    "Private School",
    "Recreation",
    "Religious",
    "Theatre",
    "Transportation",
})

# Recreation points that are an amenity inside a park, not a place.
AMENITY = re.compile(
    r"diam(?:i)?ond|playground|outdoor gym|courts?\b|pump track|dog park|"
    r"skating oval|pavill?ion|spray park|trail head|skateboard park|"
    r"outdoor ice rink|disc golf|bmx track|parkour park|goodlife",
    re.IGNORECASE,
)

# The municipal boundary's envelope (City Boundary layer, 2026-11-22), widened
# a little; the points layer carries a few City facilities outside it (the
# North Peace Rod & Gun Club at Charlie Lake), which are not the city's ground.
LAT_MIN, LAT_MAX, LON_MIN, LON_MAX = 56.20, 56.29, -120.97, -120.73

# Words kept upper-case when an all-caps park name is title-cased.
UPPER_WORDS = {"w.i", "w.i."}


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
        if word in UPPER_WORDS:
            words.append(word.upper())
        else:
            words.append("-".join(p[:1].upper() + p[1:] for p in word.split("-")))
    out = " ".join(words)
    return re.sub(r"\bSt\b(?!\.)", "St.", out)


def key_of(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.lower())


def fetch(layer: FeatureLayer, out_fields: str) -> list[dict]:
    rows: list[dict] = []
    for page in iter_features(
        layer, out_fields=out_fields, return_geometry=True, out_sr=4326, order_by="OBJECTID"
    ):
        rows.extend(page)
    return rows


def build() -> pa.Table:
    landmark_id: list[str] = []
    name: list[str] = []
    geometry_raw: list[str] = []
    property_type: list[str | None] = []
    seen: set[str] = set()

    def add(lid: str, label: str, wkt: str, kind: str | None) -> None:
        if lid in seen:
            raise RuntimeError(f"Fort St. John landmarks: repeated id {lid}")
        seen.add(lid)
        landmark_id.append(lid)
        name.append(label)
        geometry_raw.append(wkt)
        property_type.append(kind)

    park_names: set[str] = set()
    for feature in fetch(PARKS, "OBJECTID,FACILITYID,PLACENAME"):
        rec = feature.get("attributes") or {}
        fid, label = clean(rec.get("FACILITYID")), clean(rec.get("PLACENAME"))
        wkt = esri_geometry_to_wkt(feature.get("geometry"))
        if fid is None or label is None or wkt is None:
            continue
        label = title(label)
        park_names.add(key_of(label))
        add(f"fsj-park-{fid}", label, wkt, "Park")
    parks = len(landmark_id)

    for feature in fetch(POI, "OBJECTID,FACILITYID,POINTTYPE,PLACENAME"):
        rec = feature.get("attributes") or {}
        fid, label = clean(rec.get("FACILITYID")), clean(rec.get("PLACENAME"))
        kind = clean(rec.get("POINTTYPE"))
        wkt = esri_geometry_to_wkt(feature.get("geometry"))
        if fid is None or label is None or wkt is None or kind not in PLACE_TYPES:
            continue
        if kind == "Recreation" and AMENITY.search(label):
            continue
        if key_of(label) in park_names:
            continue
        geom = feature.get("geometry") or {}
        if not (LAT_MIN <= geom.get("y", 0) <= LAT_MAX and LON_MIN <= geom.get("x", 0) <= LON_MAX):
            continue
        add(f"fsj-poi-{fid}", label, wkt, kind)

    n = len(landmark_id)
    print(
        f"Fort St. John landmarks: {n} place(s) -- {parks} park(s), {n - parks} point(s)",
        file=sys.stderr,
    )
    if parks == 0:
        raise RuntimeError("Fort St. John recreation areas produced no parks")
    return pa.table(
        {
            "landmark_id": pa.array(landmark_id, type=pa.string()),
            "city": pa.array(["CAFSJ"] * n, type=pa.string()),
            "name": pa.array(name, type=pa.string()),
            "geometry_raw": pa.array(geometry_raw, type=pa.string()),
            "property_type": pa.array(property_type, type=pa.string()),
        }
    )


if __name__ == "__main__":
    emit(build())
