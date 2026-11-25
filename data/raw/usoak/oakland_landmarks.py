#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Oakland, CA's designated City Landmarks and National Register listings.

Two registers, both read live from ArcGIS layers:

* **Oakland City Landmarks**, the city's own designation register: properties
  designated by the City Council on the recommendation of the Landmarks
  Preservation Advisory Board (Oakland Planning Code ch. 17.136).  Published
  by the Bureau of Planning as the "Landmarks Board Designated" layer (35) of
  the city's `Planning_and_Zoning_Controls` MapServer on gismaps.oaklandca.gov
  ("Most of landmark information is illustrated in parcel form ... City of
  Oakland, Bureau of Planning").  No licence is stated on the service or the
  layer.  148 parcel polygons on 2026-11-20 for 147 landmark numbers
  (`LMNO`, LM001 .. LM146 with gaps); a landmark drawn on two parcels (LM068)
  is published as one row with both polygons.  The row is keyed by the
  landmark number (`oak-lm-LM014`).  `ZN_FNUM` is the designation file,
  `LM 75-221`, whose two-digit year gives `year_designated` (`CDATE` is the
  construction date, not the designation).  The layer's names are often
  typed without spaces (`Ginn(FrederickB.)house-NileClub`); a name with no
  space at all has them restored at case changes and parentheses.
* **The National Register of Historic Places** (the National Park Service's
  `cultural_resources/nrhp_locations` MapServer, points then polygons) within
  `CITY_BOUNDS["USOAK"]` and with `City = Oakland`; the box also holds
  Piedmont, Emeryville, Alameda, Berkeley and San Leandro listings.  Listed
  buildings, sites and structures, keyed `oak-nr-{NRIS_Refnum}` with
  `CertDate` as `year_designated`; districts are areas and are not
  published (Columbus's and Seattle's rule).  NPS data is a US federal work.

A building on both registers keeps both rows, as Seattle and Columbus do;
`lm_type` says which register each row is from.
"""

import re
import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_geometry_to_wkt, iter_features
from shared.ingest import emit
from shared.nrhp import national_register as shared_national_register  # noqa: E402

REGISTER = FeatureLayer(
    "https://gismaps.oaklandca.gov/server/rest/services/Planning_and_Zoning_Controls/MapServer/35"
)
REGISTER_FIELDS = "OBJECTID,LMNO,ZN_FNUM,HISTNAME,ADDRESS"


LOCAL_TYPE = "Oakland City Landmark"

# LM001 .. LM146, and a few lettered sub-landmarks (LM043A).
LANDMARK_NUMBER = re.compile(r"^LM\d{3}[A-Z]?$")
# `LM 75-221`: the designation file, year then number.
DESIGNATION_FILE = re.compile(r"^LM\s*(\d{2})-\d+")
# The Landmarks Preservation Advisory Board dates from 1973.
EARLIEST_DESIGNATION_YEAR = 1970

COLUMNS = (
    "landmark_id",
    "name",
    "geometry_raw",
    "address",
    "lm_type",
    "register_number",
    "year_designated",
)


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def respace(name: str) -> str:
    """Restore the spaces a squashed register name lost.

    Only a name with no space at all is touched, so a name typed properly
    ("Pardee (Enoch H.& George C.) House") is never re-split.
        "Ginn(FrederickB.)house-NileClub" -> "Ginn (Frederick B.) house-Nile Club"
        "YoungWomen'sChristianAssoc.building" -> "Young Women's Christian Assoc. building"
    """
    if " " in name:
        return name
    out = re.sub(r"(?<=[a-z'’])(?<!\bMc)(?=[A-Z])", " ", name)
    out = re.sub(r"(?<=[a-z]\.)(?=[A-Za-z])", " ", out)
    out = re.sub(r"\s*&\s*", " & ", out)
    out = re.sub(r"(?<=\S)\(", " (", out)
    out = re.sub(r"\)(?=\w)", ") ", out)
    out = re.sub(r",(?=\S)", ", ", out)
    return " ".join(out.split())


def designation_year(file_number: str | None) -> int | None:
    match = DESIGNATION_FILE.match(file_number or "")
    if not match:
        return None
    yy = int(match.group(1))
    year = 1900 + yy if yy >= 70 else 2000 + yy
    return year if year >= EARLIEST_DESIGNATION_YEAR else None


def fetch_all(layer: FeatureLayer, **kwargs) -> list[dict]:
    return [feature for page in iter_features(layer, **kwargs) for feature in page]


def oakland_register() -> dict[str, list]:
    features = fetch_all(REGISTER, out_fields=REGISTER_FIELDS, return_geometry=True, out_sr=4326)
    if not features:
        raise RuntimeError(f"{REGISTER.base} returned no landmarks")
    # One row per landmark number; a landmark on several parcels gets all of
    # its parcels' rings in one polygon geometry.
    grouped: dict[str, dict] = {}
    for feature in sorted(features, key=lambda f: f["attributes"]["OBJECTID"]):
        attrs = feature.get("attributes") or {}
        number = (clean(attrs.get("LMNO")) or "").upper()
        if not LANDMARK_NUMBER.match(number):
            raise RuntimeError(f"Oakland landmark number {attrs.get('LMNO')!r} is not LMnnn[a]")
        rings = (feature.get("geometry") or {}).get("rings") or []
        entry = grouped.setdefault(number, {"attrs": attrs, "rings": []})
        entry["rings"].extend(rings)

    rows: dict[str, list] = {k: [] for k in COLUMNS}
    for number, entry in sorted(grouped.items()):
        attrs = entry["attrs"]
        name = clean(attrs.get("HISTNAME"))
        wkt = esri_geometry_to_wkt({"rings": entry["rings"]}) if entry["rings"] else None
        if not name or wkt is None:
            continue
        rows["landmark_id"].append(f"oak-lm-{number}")
        rows["name"].append(respace(name))
        rows["geometry_raw"].append(wkt)
        rows["address"].append(clean(attrs.get("ADDRESS")))
        rows["lm_type"].append(LOCAL_TYPE)
        rows["register_number"].append(number)
        rows["year_designated"].append(designation_year(clean(attrs.get("ZN_FNUM"))))
    return rows


def national_register() -> dict[str, list]:
    return shared_national_register("USOAK", "Oakland", "oak", COLUMNS, required=False)


def build() -> pa.Table:
    local = oakland_register()
    national = national_register()
    data = {k: local[k] + national[k] for k in COLUMNS}
    n = len(data["landmark_id"])
    return pa.table(
        {
            "landmark_id": pa.array(data["landmark_id"], type=pa.string()),
            "name": pa.array(data["name"], type=pa.string()),
            "city": pa.array(["USOAK"] * n, type=pa.string()),
            "geometry_raw": pa.array(data["geometry_raw"], type=pa.string()),
            "address": pa.array(data["address"], type=pa.string()),
            "lm_type": pa.array(data["lm_type"], type=pa.string()),
            "register_number": pa.array(data["register_number"], type=pa.string()),
            "year_designated": pa.array(data["year_designated"], type=pa.int64()),
        }
    )


if __name__ == "__main__":
    emit(build())
