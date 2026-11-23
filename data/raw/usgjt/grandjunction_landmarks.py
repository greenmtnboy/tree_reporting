#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Grand Junction, CO's historic sites and National Register listings.

Two registers, both read live from ArcGIS layers:

* **The City's historic sites**, read from its "Historic Sites" layer
  (`Hub/City_Hub/MapServer/60` on external25-gis.gjcity.org, the same server
  as the trees; ArcGIS item 474f2237f2c44e6bbe5b6588d7638a43, licence
  "Creative Commons CC0 1.0 Universal Public Domain Dedication", the item's
  `licenseInfo`).  275 points on 2026-11-22: "Some historic sites are listed
  in the Grand Junction Register of Historic Sites, while others are located
  within designated historic districts."  The 241 labelled only `HISTORIC
  RESIDENCE` are the houses of the historic districts, named by no one, and
  are not published (districts are areas -- Durham's rule); the 34 sites with
  their own names are, keyed `gjt-hs-{OBJECTID}` (the layer has no other
  identifier; its ids run with gaps, 122..402, an edited table rather than a
  reload).  A repeated OBJECTID raises.  The layer records no designation
  date, so `year_designated` is null; `lm_type` does not claim the City
  register, since the layer does not say which sites are on it.
* **The National Register of Historic Places** (the National Park Service's
  `cultural_resources/nrhp_locations` MapServer, points then polygons) within
  `CITY_BOUNDS["USGJT"]` and with `City = Grand Junction` -- the box also
  holds the Kettle-Jens House, filed under Clifton.  Listed buildings, sites
  and structures, keyed `gjt-nr-{NRIS_Refnum}` with `CertDate` as
  `year_designated`; districts are areas and are not published.  NPS data is
  a US federal work.

Several buildings are on both (the Margery Building, Hotel St. Regis, Handy
Chapel ...); both rows are kept, as Columbus keeps them, because the two
records are separate facts.  `lm_type` says which register each row is from.
"""

import re
import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_geometry_to_wkt, iter_features
from shared.ingest import CITY_BOUNDS, emit

SITES = FeatureLayer(
    "https://external25-gis.gjcity.org/arcgis/rest/services/Hub/City_Hub/MapServer/60"
)
SITES_FIELDS = "OBJECTID,LABEL,LOCATION"
# The district houses' label: not a name.
UNNAMED_LABELS = frozenset({"historic residence"})

NRHP = "https://mapservices.nps.gov/arcgis/rest/services/cultural_resources/nrhp_locations/MapServer"
NRHP_LAYERS = (FeatureLayer(f"{NRHP}/0"), FeatureLayer(f"{NRHP}/1"))  # points first
NRHP_FIELDS = "OBJECTID,NRIS_Refnum,RESNAME,ResType,Address,City,CertDate,STATUS"

LOCAL_TYPE = "Grand Junction historic site"
NATIONAL_TYPE = "National Register of Historic Places"

COLUMNS = (
    "landmark_id",
    "name",
    "geometry_raw",
    "address",
    "lm_type",
    "register_number",
    "year_designated",
)

# Words kept upper-case when the City's all-caps labels are title-cased.
KEEP_UPPER = frozenset({"US", "CD", "R-5", "B.P.O.E.", "D&RGW"})


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def title(text: str) -> str:
    """`HOTEL ST. REGIS` -> `Hotel St. Regis`; mixed-case labels unchanged."""
    if text != text.upper():
        return text
    words = []
    for word in text.split():
        if word in KEEP_UPPER:
            words.append(word)
        elif word in {"1ST"}:
            words.append("1st")
        else:
            words.append(word[:1] + word[1:].lower())
    text = " ".join(words).replace("'S ", "'s ")
    # `POST OFFICE/WAYNE N. ASPINALL`: a capital after a slash too.
    return re.sub(r"/(\w)", lambda m: "/" + m.group(1).upper(), text)


def fetch_all(layer: FeatureLayer, **kwargs) -> list[dict]:
    return [feature for page in iter_features(layer, **kwargs) for feature in page]


def historic_sites() -> dict[str, list]:
    rows: dict[str, list] = {k: [] for k in COLUMNS}
    features = fetch_all(SITES, out_fields=SITES_FIELDS, return_geometry=True, out_sr=4326)
    if not features:
        raise RuntimeError(f"{SITES.base} returned no sites")
    seen: set[int] = set()
    for feature in features:
        attrs = feature.get("attributes") or {}
        oid = attrs.get("OBJECTID")
        if oid in seen:
            raise RuntimeError(f"Grand Junction Historic Sites OBJECTID {oid} is repeated")
        seen.add(oid)
        label = clean(attrs.get("LABEL"))
        wkt = esri_geometry_to_wkt(feature.get("geometry"))
        if not label or label.lower() in UNNAMED_LABELS or wkt is None:
            continue
        address = clean(attrs.get("LOCATION"))
        rows["landmark_id"].append(f"gjt-hs-{oid}")
        rows["name"].append(title(label))
        rows["geometry_raw"].append(wkt)
        rows["address"].append(title(address) if address else None)
        rows["lm_type"].append(LOCAL_TYPE)
        rows["register_number"].append(None)
        rows["year_designated"].append(None)
    return rows


def national_register() -> dict[str, list]:
    lat_min, lat_max, lon_min, lon_max = CITY_BOUNDS["USGJT"]
    box = {
        "geometry": f"{lon_min},{lat_min},{lon_max},{lat_max}",
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
    }
    seen: set[str] = set()
    rows: dict[str, list] = {k: [] for k in COLUMNS}
    for layer in NRHP_LAYERS:
        for feature in fetch_all(
            layer, out_fields=NRHP_FIELDS, return_geometry=True, out_sr=4326, extra_params=box
        ):
            attrs = feature.get("attributes") or {}
            ref = clean(attrs.get("NRIS_Refnum"))
            name = clean(attrs.get("RESNAME"))
            wkt = esri_geometry_to_wkt(feature.get("geometry"))
            if not ref or not name or wkt is None or ref in seen:
                continue
            if (clean(attrs.get("City")) or "").lower() != "grand junction":
                continue
            if clean(attrs.get("STATUS")) != "Listed":
                continue
            if (clean(attrs.get("ResType")) or "").lower() == "district":
                continue
            seen.add(ref)
            cert = clean(attrs.get("CertDate")) or ""
            rows["landmark_id"].append(f"gjt-nr-{ref}")
            rows["name"].append(name)
            rows["geometry_raw"].append(wkt)
            rows["address"].append(clean(attrs.get("Address")))
            rows["lm_type"].append(NATIONAL_TYPE)
            rows["register_number"].append(ref)
            rows["year_designated"].append(int(cert[:4]) if cert[:4].isdigit() else None)
    return rows


def build() -> pa.Table:
    local = historic_sites()
    national = national_register()
    data = {k: local[k] + national[k] for k in COLUMNS}
    n = len(data["landmark_id"])
    return pa.table(
        {
            "landmark_id": pa.array(data["landmark_id"], type=pa.string()),
            "name": pa.array(data["name"], type=pa.string()),
            "city": pa.array(["USGJT"] * n, type=pa.string()),
            "geometry_raw": pa.array(data["geometry_raw"], type=pa.string()),
            "address": pa.array(data["address"], type=pa.string()),
            "lm_type": pa.array(data["lm_type"], type=pa.string()),
            "register_number": pa.array(data["register_number"], type=pa.string()),
            "year_designated": pa.array(data["year_designated"], type=pa.int64()),
        }
    )


if __name__ == "__main__":
    emit(build())
