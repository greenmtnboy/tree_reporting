#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Durham, NC's designated historic landmarks.

Source: two layers of the City-County Planning Department's
`PublicServices/Planning` MapServer on webgis2.durhamnc.gov, the same server
that hosts the tree inventory -- official designation registers, the
runbook's first preference, so there is no Nominatim geocoding, no committed
CSV and nothing to stage.

* **Local Historic Landmarks** (layer 1): the landmarks designated by
  ordinance by the City or County of Durham, 97 rows on 2026-11-11, every one
  `DESIGSTATUS = 'Active'`.  A row is a designated *parcel*, drawn as its
  polygon, so a landmark spanning several parcels (the West Village tobacco
  complex, `CASENO` LD01-05) is several rows with one name; `GlobalID` is the
  key, because `CASENO` repeats across those parcels.  `DATEADOPTED` is the
  designation date; the district is the local historic district where there
  is one, else the National Register district, and `N/A` is missing.
* **National Historic Landmarks** (layer 2): despite the layer's name, the
  county's *individual* National Register listings (47 rows, two of them true
  National Historic Landmarks marked `(NHL)` in the name -- Duke Homestead and
  the Pauli Murray Family Home).  The key is `SITE_ID`, the NC State Historic
  Preservation Office survey number (`DH0008`), unique and never null;
  `YearListed` is the listing year.

About a dozen buildings are on both registers (the Forbus House, Watts &
Yuille); each register's row is kept and `lm_type` says which it is, because
the two designations are separate facts with separate dates.  The National
Register historic *districts* (layers 10 and 11) are areas, not landmarks,
and are not published.
"""

import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import (
    FeatureLayer,
    esri_geometry_to_wkt,
    esri_ms_to_date,
    iter_features,
)
from shared.ingest import emit

PLANNING = (
    "https://webgis2.durhamnc.gov/server/rest/services/PublicServices/Planning/MapServer"
)
LOCAL_LANDMARKS = FeatureLayer(f"{PLANNING}/1")
NATIONAL_REGISTER = FeatureLayer(f"{PLANNING}/2")

LOCAL_FIELDS = "GlobalID,NAME,DATEADOPTED,FULLADDR,LOCALDIST,NATIONALDIST,DESIGSTATUS"
NATIONAL_FIELDS = "SITE_ID,SITE_NAME,YearListed"

LOCAL_TYPE = "Local Historic Landmark"
NATIONAL_TYPE = "National Register of Historic Places"


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text if text and text.upper() != "N/A" else None


def parse_year(value) -> int | None:
    text = clean(value)
    if text is None:
        return None
    if not (text.isdigit() and len(text) == 4):
        raise RuntimeError(f"Durham National Register YearListed {text!r} is not a year")
    return int(text)


def read(layer: FeatureLayer, out_fields: str) -> list[dict]:
    features: list[dict] = []
    for page in iter_features(
        layer, out_fields=out_fields, return_geometry=True, order_by="OBJECTID"
    ):
        features.extend(page)
    return features


def transform(local: list[dict], national: list[dict]) -> pa.Table:
    cols: dict[str, list] = {
        k: []
        for k in (
            "landmark_id", "name", "geometry_raw", "address", "neighborhood",
            "lm_type", "designation_date", "year_designated",
        )
    }

    def add(landmark_id, name, wkt, address, neighborhood, lm_type, designated, year):
        cols["landmark_id"].append(landmark_id)
        cols["name"].append(name)
        cols["geometry_raw"].append(wkt)
        cols["address"].append(address)
        cols["neighborhood"].append(neighborhood)
        cols["lm_type"].append(lm_type)
        cols["designation_date"].append(designated)
        cols["year_designated"].append(year)

    for feature in local:
        attrs = feature.get("attributes") or {}
        status = clean(attrs.get("DESIGSTATUS"))
        if status != "Active":
            raise RuntimeError(
                f"Durham landmark DESIGSTATUS {status!r} is new; decide whether it is designated"
            )
        gid = clean(attrs.get("GlobalID"))
        name = clean(attrs.get("NAME"))
        wkt = esri_geometry_to_wkt(feature.get("geometry"))
        if not gid or not name or wkt is None:
            continue
        adopted = esri_ms_to_date(attrs.get("DATEADOPTED"))
        add(
            f"dur-lhl-{gid.strip('{}').lower()}",
            name,
            wkt,
            clean(attrs.get("FULLADDR")),
            clean(attrs.get("LOCALDIST")) or clean(attrs.get("NATIONALDIST")),
            LOCAL_TYPE,
            adopted,
            adopted.year if adopted else None,
        )

    for feature in national:
        attrs = feature.get("attributes") or {}
        site_id = clean(attrs.get("SITE_ID"))
        name = clean(attrs.get("SITE_NAME"))
        wkt = esri_geometry_to_wkt(feature.get("geometry"))
        if not site_id or not name or wkt is None:
            continue
        add(
            f"dur-nr-{site_id.lower()}",
            name,
            wkt,
            None,
            None,
            NATIONAL_TYPE,
            None,
            parse_year(attrs.get("YearListed")),
        )

    n = len(cols["landmark_id"])
    if len(set(cols["landmark_id"])) != n:
        raise RuntimeError("Durham landmark ids are not unique")
    return pa.table(
        {
            "landmark_id": pa.array(cols["landmark_id"], type=pa.string()),
            "name": pa.array(cols["name"], type=pa.string()),
            "city": pa.array(["USDUR"] * n, type=pa.string()),
            "geometry_raw": pa.array(cols["geometry_raw"], type=pa.string()),
            "address": pa.array(cols["address"], type=pa.string()),
            "neighborhood": pa.array(cols["neighborhood"], type=pa.string()),
            "lm_type": pa.array(cols["lm_type"], type=pa.string()),
            "designation_date": pa.array(cols["designation_date"], type=pa.date32()),
            "year_designated": pa.array(cols["year_designated"], type=pa.int64()),
        }
    )


if __name__ == "__main__":
    emit(
        transform(
            read(LOCAL_LANDMARKS, LOCAL_FIELDS),
            read(NATIONAL_REGISTER, NATIONAL_FIELDS),
        )
    )
