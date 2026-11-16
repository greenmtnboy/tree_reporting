#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Eugene, OR's designated historic landmarks and National Register listings.

Source: "Historic Sites - public view" (ArcGIS item
e012d77a3a624a1e9f1ce01c8bb0ae8d, owner `Thea.D.Evans_PDD`, credited to the
City of Eugene Planning Division), the hosted layer
`Historic_Sites_(public_view)/FeatureServer/0` on the city's own ArcGIS Online
organisation -- the one that hosts the trees.  It is the city's cultural
resource inventory: 4,131 points on 2026-11-16, most of them surveyed
buildings with no designation.  Two flags say which are designated:

    is_city_landmark = 1        160   City of Eugene Historic Landmark
    on_national_register = 1    167   National Register of Historic Places
    either                      213   (114 are both)

Only those 213 are read.  The layer also records the end of a designation
(`LM_removal_effective_date`, `NR_removal_effective_date`) and a demolition
(`demolition_effective_date`), written as the *string* `NULL` when empty; a
row whose every designation has been removed, or which has been demolished,
is not published (none is today).  Of the 213:

* 27 have neither a historic nor a common name: contributing buildings of
  the Blair Boulevard and Skinner Butte districts recorded by address only
  (eight of them stacked on one point at 235 E 2nd Ave).  They are not
  published -- a pin named after its street address is not a landmark.
* 2 are National Register multiple-property submittals (West University,
  River Road), documents rather than places, parked at a point in the
  Pacific; the `CITY_BOUNDS` check drops them.

So 184 are published.  The key is `cultural_resource_id`, the city's
inventory number (unique over the layer), as `eug-hs-{id}`.  `historic_name`
is the name, falling back to `common_name` (the current use: `Tiny Tavern`);
both are upper-case and padded, so they are trimmed and re-cased.
`construction_year` is text (`1871`, `c. 1925`) and its four-digit year is
`year_built`; `year_designated` is the year of the earlier of the two
designations (`LM_effective_date` / `NR_effective_date`, `M/D/YYYY`).
`lm_type` names the register, or both.  The style, significance ranking and
description have no columns in the landmark schema and are not published.
Eugene's historic districts (the `HistoricDistricts` service) are areas, not
landmarks, and are not read (Durham's rule).
"""

import re
import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, iter_features
from shared.ingest import CITY_BOUNDS, emit, make_point_wkt

LAYER = FeatureLayer(
    "https://services3.arcgis.com/F7NiRLGNbA2hh7gE/arcgis/rest/services/"
    "Historic_Sites_(public_view)/FeatureServer/0"
)

OUT_FIELDS = ",".join(
    (
        "OBJECTID",
        "cultural_resource_id",
        "historic_name",
        "common_name",
        "construction_year",
        "address",
        "is_city_landmark",
        "on_national_register",
        "LM_effective_date",
        "LM_removal_effective_date",
        "NR_effective_date",
        "NR_removal_effective_date",
        "demolition_effective_date",
    )
)
WHERE = "is_city_landmark = 1 OR on_national_register = 1"

CITY_TYPE = "City of Eugene historic landmark"
NATIONAL_TYPE = "National Register of Historic Places"
BOTH_TYPE = "City of Eugene historic landmark and National Register of Historic Places"

DIRECTIONS = frozenset({"N", "S", "E", "W", "NE", "NW", "SE", "SW"})
ROMAN = frozenset({"II", "III", "IV"})

COLUMNS = ("landmark_id", "name", "geometry_raw", "address", "year_built", "lm_type", "year_designated")


def clean(value) -> str | None:
    """Trimmed text, or None for blank and for the layer's literal `NULL`."""
    if value is None:
        return None
    text = " ".join(str(value).split())
    if not text or text.upper() == "NULL":
        return None
    return text


def _recase_word(word: str) -> str:
    if word.upper() in ROMAN:
        return word.upper()
    # Initials (`C.S.`, `F.`) stay capitals.
    if re.fullmatch(r"(?:[A-Za-z]\.)+", word):
        return word.upper()
    parts = re.split(r"([-/])", word)
    return "".join(p[:1].upper() + p[1:].lower() if p not in "-/" else p for p in parts)


def recase(text: str | None) -> str | None:
    """`SAM BOND'S GARAGE` -> `Sam Bond's Garage`; mixed case is left alone."""
    if text is None:
        return None
    if not text.isupper():
        return text
    return " ".join(_recase_word(w) for w in text.split())


def format_address(raw) -> str | None:
    """`1611 LINCOLN ST` -> `1611 Lincoln St`; `740 W 13TH AVE` -> `740 W 13th Ave`."""
    text = clean(raw)
    if text is None:
        return None
    return " ".join(
        w.lower() if w[0].isdigit() else (w.upper() if w.upper() in DIRECTIONS else w.capitalize())
        for w in text.split()
    )


def first_year(value) -> int | None:
    match = re.search(r"\d{4}", str(value or ""))
    return int(match.group()) if match else None


def designation_year(value) -> int | None:
    """`11/24/1975` -> 1975."""
    text = clean(value)
    match = re.fullmatch(r"\d{1,2}/\d{1,2}/(\d{4})", text or "")
    return int(match.group(1)) if match else None


def build() -> pa.Table:
    lat_min, lat_max, lon_min, lon_max = CITY_BOUNDS["USEUG"]
    rows: dict[str, list] = {k: [] for k in COLUMNS}
    seen: set[str] = set()
    read = unnamed = outside = withdrawn = 0
    features = [
        feature
        for page in iter_features(
            LAYER, out_fields=OUT_FIELDS, where=WHERE, return_geometry=True, out_sr=4326
        )
        for feature in page
    ]
    if not features:
        raise RuntimeError(f"{LAYER.base} returned no designated sites")
    for feature in sorted(features, key=lambda f: f["attributes"]["cultural_resource_id"] or 0):
        attrs = feature.get("attributes") or {}
        read += 1
        city = attrs.get("is_city_landmark") == 1 and clean(attrs.get("LM_removal_effective_date")) is None
        national = (
            attrs.get("on_national_register") == 1
            and clean(attrs.get("NR_removal_effective_date")) is None
        )
        if not (city or national) or clean(attrs.get("demolition_effective_date")) is not None:
            withdrawn += 1
            continue
        name = recase(clean(attrs.get("historic_name")) or clean(attrs.get("common_name")))
        if not name:
            unnamed += 1
            continue
        geom = feature.get("geometry") or {}
        lon, lat = geom.get("x"), geom.get("y")
        wkt = make_point_wkt(lon, lat)
        if wkt is None or not (lat_min <= lat < lat_max and lon_min <= lon < lon_max):
            outside += 1
            continue
        resource_id = attrs.get("cultural_resource_id")
        if resource_id is None:
            raise RuntimeError(f"Eugene historic site OBJECTID {attrs.get('OBJECTID')} has no id")
        landmark_id = f"eug-hs-{int(resource_id)}"
        if landmark_id in seen:
            raise RuntimeError(f"Eugene cultural_resource_id {resource_id} repeats")
        seen.add(landmark_id)
        years = [
            y
            for y in (
                designation_year(attrs.get("LM_effective_date")) if city else None,
                designation_year(attrs.get("NR_effective_date")) if national else None,
            )
            if y is not None
        ]
        rows["landmark_id"].append(landmark_id)
        rows["name"].append(name)
        rows["geometry_raw"].append(wkt)
        rows["address"].append(format_address(attrs.get("address")))
        rows["year_built"].append(first_year(attrs.get("construction_year")))
        rows["lm_type"].append(BOTH_TYPE if city and national else CITY_TYPE if city else NATIONAL_TYPE)
        rows["year_designated"].append(min(years) if years else None)
    print(
        f"Eugene landmarks: {read} designated sites read, {len(seen)} published; "
        f"skipped {unnamed} without a name, {outside} outside the city, "
        f"{withdrawn} withdrawn or demolished",
        file=sys.stderr,
    )
    n = len(rows["landmark_id"])
    return pa.table(
        {
            "landmark_id": pa.array(rows["landmark_id"], type=pa.string()),
            "name": pa.array(rows["name"], type=pa.string()),
            "city": pa.array(["USEUG"] * n, type=pa.string()),
            "geometry_raw": pa.array(rows["geometry_raw"], type=pa.string()),
            "address": pa.array(rows["address"], type=pa.string()),
            "year_built": pa.array(rows["year_built"], type=pa.int64()),
            "lm_type": pa.array(rows["lm_type"], type=pa.string()),
            "year_designated": pa.array(rows["year_designated"], type=pa.int64()),
        }
    )


if __name__ == "__main__":
    emit(build())
