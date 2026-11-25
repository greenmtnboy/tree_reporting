#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Orland Park, Illinois's historic buildings and National Register listings.

Two registers, both read live from ArcGIS layers:

* **The Old Orland Historic District's contributing structures**, the
  Village's own point layer `OOHContributingStructure_Point/FeatureServer/0`
  (ArcGIS item 99feff2aaed940efab4e39b8898e128a, owner `zzimmerman_vop`,
  Village of Orland Park), 16 points on 2026-11-23, unedited since 2022: the
  buildings the Village's locally designated district protects, one (the
  Orland Park School) outside its boundary.  No licence stated; displayed
  with attribution only, not licensed for reuse (the item's `licenseInfo`
  and `accessInformation` are empty).  The six named only `Residence` or
  `Former Residence` are the district's unnamed houses and are not published
  (districts are areas -- Durham's rule); the ten with their own names are,
  keyed `opk-ooh-{OBJECTID}` (the building-footprint id the points were made
  from; a repeat raises).  The layer records no designation date.
* **The National Register of Historic Places** (the National Park Service's
  `cultural_resources/nrhp_locations` MapServer, points then polygons) within
  `CITY_BOUNDS["USOPK"]` and with `City = Orland Park` -- the box also holds
  a Tinley Park listing.  Listed buildings, sites and structures, keyed
  `opk-nr-{NRIS_Refnum}` with `CertDate` as `year_designated`; districts are
  areas and are not published.  NPS data is a US federal work.

The Twin Tower Sanctuary and the John Humphrey House are on both; both rows
are kept, as Columbus keeps them, because the two records are separate
facts.  `lm_type` says which register each row is from.
"""

import re
import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_geometry_to_wkt, iter_features  # noqa: E402
from shared.ingest import emit  # noqa: E402
from shared.nrhp import national_register as shared_national_register  # noqa: E402

DISTRICT = FeatureLayer(
    "https://services3.arcgis.com/sVDuYsm37AMvFL49/arcgis/rest/services/"
    "OOHContributingStructure_Point/FeatureServer/0"
)
DISTRICT_FIELDS = "OBJECTID_1,OBJECTID,Address,HistName"
# The district houses' labels: not names.
UNNAMED = frozenset({"residence", "former residence"})

NRHP = "https://mapservices.nps.gov/arcgis/rest/services/cultural_resources/nrhp_locations/MapServer"

LOCAL_TYPE = "Old Orland Historic District contributing structure"

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


def fetch_all(layer: FeatureLayer, **kwargs) -> list[dict]:
    return [feature for page in iter_features(layer, **kwargs) for feature in page]


def district_structures() -> dict[str, list]:
    rows: dict[str, list] = {k: [] for k in COLUMNS}
    features = fetch_all(
        DISTRICT, out_fields=DISTRICT_FIELDS, return_geometry=True, out_sr=4326,
        order_by="OBJECTID_1",
    )
    if not features:
        raise RuntimeError(f"{DISTRICT.base} returned no structures")
    seen: set[int] = set()
    for feature in features:
        attrs = feature.get("attributes") or {}
        oid = attrs.get("OBJECTID")
        if oid is None or oid in seen:
            raise RuntimeError(f"Old Orland contributing structure OBJECTID {oid!r} is repeated or null")
        seen.add(oid)
        name = clean(attrs.get("HistName"))
        wkt = esri_geometry_to_wkt(feature.get("geometry"))
        if not name or name.lower() in UNNAMED or wkt is None:
            continue
        address = clean(attrs.get("Address"))
        if address:
            address = re.sub(r"\s*\(NRHP\)$", "", address)
        rows["landmark_id"].append(f"opk-ooh-{oid}")
        rows["name"].append(name)
        rows["geometry_raw"].append(wkt)
        rows["address"].append(address)
        rows["lm_type"].append(LOCAL_TYPE)
        rows["register_number"].append(None)
        rows["year_designated"].append(None)
    return rows


def national_register() -> dict[str, list]:
    return shared_national_register("USOPK", "Orland Park", "opk", COLUMNS, required=False)


def build() -> pa.Table:
    local = district_structures()
    national = national_register()
    data = {k: local[k] + national[k] for k in COLUMNS}
    n = len(data["landmark_id"])
    return pa.table(
        {
            "landmark_id": pa.array(data["landmark_id"], type=pa.string()),
            "name": pa.array(data["name"], type=pa.string()),
            "city": pa.array(["USOPK"] * n, type=pa.string()),
            "geometry_raw": pa.array(data["geometry_raw"], type=pa.string()),
            "address": pa.array(data["address"], type=pa.string()),
            "lm_type": pa.array(data["lm_type"], type=pa.string()),
            "register_number": pa.array(data["register_number"], type=pa.string()),
            "year_designated": pa.array(data["year_designated"], type=pa.int64()),
        }
    )


if __name__ == "__main__":
    emit(build())
