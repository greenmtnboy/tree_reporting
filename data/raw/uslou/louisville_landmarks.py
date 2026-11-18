#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Louisville Metro's individual landmarks and National Register listings.

Two registers, both read live from ArcGIS layers:

* **Louisville Metro Individual Landmarks**, designated by the Metro
  Landmarks Commission (Planning & Design Services), from LOJIC's "Jefferson
  County KY Historic Preservation Districts" layer
  (`LojicSolutions/OpenDataDevelopment/MapServer/4` on gis.lojic.org, ArcGIS
  item db2b664ddfee430fad4fa1da4123be30), listed on Louisville Metro Open Data
  (data.louisvilleky.gov) under its Terms of Use, which dedicate the
  platform's datasets under the ODC Public Domain Dedication and License
  (PDDL).  143 parcel polygons on 2026-11-18: 125 with `PRESDIST_NAME` =
  INDIVIDUAL LANDMARK, the rest the seven preservation districts (areas, not
  landmarks; not read).  One landmark is often several parcels (Whiskey Row
  is 8, Farmington 6 lots), so rows are grouped by `KEY_NUMBER`, the
  landmark's number on the Commission's map (the commission id is not a key:
  it is repeated across landmarks and sometimes holds the name), or by name
  for the three 2024 designations that have none; 90-odd landmarks.  Keyed
  `lou-lm-{KEY_NUMBER}` (or `lou-lm-{name slug}`), geometry the parcels'
  union as one MULTIPOLYGON, `YEAR_DESIGNATED` the Commission's year.
* **The National Register of Historic Places** (the National Park Service's
  `cultural_resources/nrhp_locations` MapServer, points then polygons) with
  `County = Jefferson` and `State = KENTUCKY` -- Louisville Metro is the
  consolidated city-county, so the county is the city (Philadelphia's rule;
  the box also holds New Albany and Jeffersonville, Indiana, and Bullitt and
  Oldham County listings with a Louisville postal address).  Listed
  buildings, sites and structures, keyed `lou-nr-{NRIS_Refnum}` with
  `CertDate` as `year_designated`; districts are areas and are not
  published.  NPS data is a US federal work.

A Metro landmark whose `NATIONAL_REGISTER_TYPE` names an individual listing
(`NR-80001587`, `NHL-66000359`) that the NPS layer publishes is one place on
two registers: it is published once, as the Metro landmark with its parcel
outline, marked as on both (`lm_type`) with the earlier designation year, and
the NPS row is not repeated.
"""

import re
import sys
from collections import defaultdict
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_geometry_to_wkt, iter_features
from shared.ingest import CITY_BOUNDS, emit

METRO = FeatureLayer(
    "https://gis.lojic.org/maps/rest/services/LojicSolutions/OpenDataDevelopment/MapServer/4",
    timeout=180,
)
METRO_FIELDS = (
    "OBJECTID,PRESDIST_NAME,HISTORIC_NAME,YEAR_DESIGNATED,NATIONAL_REGISTER_TYPE,"
    "METRO_LANDMARK_COMMISSION_ID,KEY_NUMBER"
)
METRO_WHERE = "PRESDIST_NAME = 'INDIVIDUAL LANDMARK'"

NRHP = "https://mapservices.nps.gov/arcgis/rest/services/cultural_resources/nrhp_locations/MapServer"
NRHP_LAYERS = (FeatureLayer(f"{NRHP}/0"), FeatureLayer(f"{NRHP}/1"))  # points first
NRHP_FIELDS = "OBJECTID,NRIS_Refnum,RESNAME,ResType,Address,County,State,CertDate,STATUS"
NRHP_WHERE = "County = 'Jefferson' AND State = 'KENTUCKY'"

METRO_TYPE = "Louisville Metro Individual Landmark"
NATIONAL_TYPE = "National Register of Historic Places"
BOTH_TYPE = "Louisville Metro Individual Landmark and National Register of Historic Places"

COLUMNS = (
    "landmark_id",
    "name",
    "geometry_raw",
    "address",
    "lm_type",
    "register_number",
    "year_designated",
)

# `NR-80001587`, `NHL-66000359`, `NR-75000773-NRD`: an individual listing's
# NRIS reference number.  `NRD-...` (a contributing building in a district)
# and `NOT-ON-NATIONAL-REG*` are not.
NR_REF = re.compile(r"^(?:NR|NHL)-(\d{8})\b")
LOT_SUFFIX = re.compile(r"\s*\(LOT \d+\)\s*$", re.IGNORECASE)
SMALL_WORDS = frozenset({"of", "and", "the", "at", "on", "in", "for", "de"})
ROMAN = re.compile(r"^[IVX]+$")
ACRONYMS = frozenset({"YMCA", "YWCA", "USA", "KY", "WHAS", "WLKY"})


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def year(value) -> int | None:
    text = clean(value) or ""
    return int(text[:4]) if text[:4].isdigit() else None


def display_name(upper: str) -> str:
    """`ST PATRICK'S ROMAN CATH. CHURCH` -> `St Patrick's Roman Cath. Church`."""
    words = []
    for i, word in enumerate(upper.split()):
        lower = word.lower()
        if i and lower in SMALL_WORDS:
            words.append(lower)
        elif word in ACRONYMS or (ROMAN.match(word) and len(word) > 1):
            words.append(word)
        else:
            # Capitalise after a leading bracket, slash or hyphen too.
            words.append(re.sub(r"(^|[(/\-])([a-z])", lambda m: m.group(1) + m.group(2).upper(), lower))
    return " ".join(words)


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def bounding_box() -> dict[str, str]:
    lat_min, lat_max, lon_min, lon_max = CITY_BOUNDS["USLOU"]
    return {
        "geometry": f"{lon_min},{lat_min},{lon_max},{lat_max}",
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
    }


def national_register() -> dict[str, dict]:
    """NRIS reference -> row, for the listed, non-district Jefferson County entries."""
    out: dict[str, dict] = {}
    for layer in NRHP_LAYERS:
        for page in iter_features(
            layer,
            out_fields=NRHP_FIELDS,
            where=NRHP_WHERE,
            return_geometry=True,
            out_sr=4326,
            extra_params=bounding_box(),
        ):
            for feature in page:
                attrs = feature.get("attributes") or {}
                ref = clean(attrs.get("NRIS_Refnum"))
                name = clean(attrs.get("RESNAME"))
                wkt = esri_geometry_to_wkt(feature.get("geometry"))
                if not ref or not name or wkt is None or ref in out:
                    continue
                if clean(attrs.get("STATUS")) != "Listed":
                    continue
                if (clean(attrs.get("ResType")) or "").lower() == "district":
                    continue
                out[ref] = {
                    "name": name,
                    "geometry_raw": wkt,
                    "address": clean(attrs.get("Address")),
                    "year": year(attrs.get("CertDate")),
                }
    if not out:
        raise RuntimeError("no National Register listings read for Jefferson County, KY")
    return out


def metro_landmarks() -> list[dict]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for page in iter_features(
        METRO, out_fields=METRO_FIELDS, where=METRO_WHERE, return_geometry=True, out_sr=4326
    ):
        for feature in page:
            attrs = feature.get("attributes") or {}
            name = clean(attrs.get("HISTORIC_NAME"))
            if not name or not (feature.get("geometry") or {}).get("rings"):
                continue
            key = attrs.get("KEY_NUMBER")
            groups[str(key) if key is not None else slug(LOT_SUFFIX.sub("", name))].append(feature)
    if not groups:
        raise RuntimeError(f"{METRO.base} returned no individual landmarks")

    out = []
    for key, features in sorted(groups.items()):
        attrs = [f["attributes"] for f in features]
        names = sorted({LOT_SUFFIX.sub("", clean(a["HISTORIC_NAME"])) for a in attrs}, key=len)
        rings = [ring for f in features for ring in f["geometry"]["rings"]]
        years = [y for y in (year(a.get("YEAR_DESIGNATED")) for a in attrs) if y is not None]
        refs = sorted(
            {m.group(1) for a in attrs if (m := NR_REF.match(clean(a.get("NATIONAL_REGISTER_TYPE")) or ""))}
        )
        ids = sorted(
            {
                (clean(a.get("METRO_LANDMARK_COMMISSION_ID")) or "").rstrip("*")
                for a in attrs
                if re.match(r"^(JF|LM)", clean(a.get("METRO_LANDMARK_COMMISSION_ID")) or "")
            }
        )
        out.append(
            {
                "landmark_id": f"lou-lm-{key}",
                "name": display_name(names[0]),
                "geometry_raw": esri_geometry_to_wkt({"rings": rings}),
                "year": min(years) if years else None,
                "nr_refs": refs,
                "register_number": ", ".join(ids) or None,
            }
        )
    return out


def build() -> pa.Table:
    national = national_register()
    metro = metro_landmarks()
    rows: dict[str, list] = {k: [] for k in COLUMNS}
    claimed: set[str] = set()
    both = 0
    for lm in metro:
        if lm["geometry_raw"] is None:
            continue
        listed = [r for r in lm["nr_refs"] if r in national]
        claimed.update(listed)
        years = [lm["year"]] + [national[r]["year"] for r in listed]
        years = [y for y in years if y is not None]
        both += bool(listed)
        rows["landmark_id"].append(lm["landmark_id"])
        rows["name"].append(lm["name"])
        rows["geometry_raw"].append(lm["geometry_raw"])
        rows["address"].append(national[listed[0]]["address"] if listed else None)
        rows["lm_type"].append(BOTH_TYPE if listed else METRO_TYPE)
        rows["register_number"].append(
            ", ".join(x for x in [lm["register_number"], *listed] if x) or None
        )
        rows["year_designated"].append(min(years) if years else None)
    metro_n = len(rows["landmark_id"])
    for ref, nr in sorted(national.items()):
        if ref in claimed:
            continue
        rows["landmark_id"].append(f"lou-nr-{ref}")
        rows["name"].append(nr["name"])
        rows["geometry_raw"].append(nr["geometry_raw"])
        rows["address"].append(nr["address"])
        rows["lm_type"].append(NATIONAL_TYPE)
        rows["register_number"].append(ref)
        rows["year_designated"].append(nr["year"])
    if len(set(rows["landmark_id"])) != len(rows["landmark_id"]):
        raise RuntimeError("Louisville landmark ids repeat")
    print(
        f"Louisville landmarks: {metro_n} Metro individual landmarks ({both} also on "
        f"the National Register), {len(rows['landmark_id']) - metro_n} other National "
        "Register listings",
        file=sys.stderr,
    )
    n = len(rows["landmark_id"])
    return pa.table(
        {
            "landmark_id": pa.array(rows["landmark_id"], type=pa.string()),
            "name": pa.array(rows["name"], type=pa.string()),
            "city": pa.array(["USLOU"] * n, type=pa.string()),
            "geometry_raw": pa.array(rows["geometry_raw"], type=pa.string()),
            "address": pa.array(rows["address"], type=pa.string()),
            "lm_type": pa.array(rows["lm_type"], type=pa.string()),
            "register_number": pa.array(rows["register_number"], type=pa.string()),
            "year_designated": pa.array(rows["year_designated"], type=pa.int64()),
        }
    )


if __name__ == "__main__":
    emit(build())
