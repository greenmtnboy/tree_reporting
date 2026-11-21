#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Guelph's (Ontario) heritage register: designated and listed properties.

No licence stated; displayed with attribution only, not licensed for reuse.
The layer is the City of Guelph's own (`CulturalHeritageResources`, item
9ff50dc2055f435c92251cb83282da7f, owned by a City planning account and shown
in the "Cultural Heritage Resources" app listed on the Guelph GeoDataHub,
https://explore.guelph.ca/apps/cityofguelph::cultural-heritage-resources),
but neither the item nor the hub's catalogue entry names a licence -- the
hub's Open Data Licence is attached to datasets such as Trees one by one, and
this entry's `license` is empty.

**Layer 0 (`HI_Properties`, ~4.5k points) is the register plus the
inventories behind it.**  `WORK_` says which: Designated - Individual (Part IV
of the Ontario Heritage Act, 129), Designated - HCD (inside the Brooklyn and
College Hill Heritage Conservation District, Part V, 153), Designated - HCD
and Individual (10), and Municipal Register (listed, non-designated, 1,734)
are the Heritage Register and are kept.  The Couling Inventory (2,247, a
1970s architectural survey), Potential Heritage Resource, Demolished and
blank rows are not on the register and are dropped; so is any kept row whose
`StatusCode` is Demolished or Closed.

**Layer 1 (`CulturalHeritageLandscapes`, 41 polygons)** adds the landscapes
designated under Part IV or V (`STAT` starting "Designated": the Brooklyn and
College Hill HCD, Homewood, Riverslea, Marcolongo Farm).  The rest are
candidate landscapes from the City's Cultural Heritage Action Plan and are
dropped.

**Most register entries are known only by their address**, which is the
name; where the register names the building in the address field
(`34 Arboretum Rd - UofG - Alumni House`) the name rides along.
`property_type` is the register's own category (`WORK_`, or the landscape's
`STAT`).  Ids are the layers' OBJECTIDs (`glp-hp-{n}`, `glp-chl-{n}`): the
layers carry no GlobalID and `FolderRSN` (the permit-system folder) is not
unique.
"""

import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import (
    FeatureLayer,
    esri_geometry_to_wkt,
    iter_features,
)
from shared.ingest import emit

SERVICE = (
    "https://services5.arcgis.com/k3dd78JyG9GFoZHG/arcgis/rest/services/"
    "CulturalHeritageResources/FeatureServer"
)
PROPERTIES = FeatureLayer(f"{SERVICE}/0", timeout=180)
LANDSCAPES = FeatureLayer(f"{SERVICE}/1", timeout=180)

REGISTER_CATEGORIES = (
    "Designated - Individual",
    "Designated - HCD",
    "Designated - HCD and Individual",
    "Municipal Register",
)
GONE_STATUS = {"Demolished", "Closed"}


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def _quoted(values) -> str:
    return ", ".join("'" + v.replace("'", "''") + "'" for v in values)


def properties() -> dict[str, list]:
    out: dict[str, list] = {k: [] for k in ("id", "name", "wkt", "address", "type")}
    where = f"WORK_ IN ({_quoted(REGISTER_CATEGORIES)})"
    for page in iter_features(
        PROPERTIES,
        out_fields="OBJECTID,ADDRESS,WORK_,StatusCode",
        where=where,
        return_geometry=True,
    ):
        for feature in page:
            rec = feature.get("attributes") or {}
            if clean(rec.get("StatusCode")) in GONE_STATUS:
                continue
            address = clean(rec.get("ADDRESS"))
            wkt = esri_geometry_to_wkt(feature.get("geometry"))
            if address is None or wkt is None or rec.get("OBJECTID") is None:
                continue
            out["id"].append(f"glp-hp-{int(rec['OBJECTID'])}")
            out["name"].append(address)
            out["wkt"].append(wkt)
            out["address"].append(address.split(" - ")[0].strip())
            out["type"].append(clean(rec.get("WORK_")))
    return out


def landscapes() -> dict[str, list]:
    out: dict[str, list] = {k: [] for k in ("id", "name", "wkt", "address", "type")}
    for page in iter_features(
        LANDSCAPES,
        out_fields="OBJECTID,LABEL,STAT",
        where="STAT LIKE 'Designated%'",
        return_geometry=True,
    ):
        for feature in page:
            rec = feature.get("attributes") or {}
            label = clean(rec.get("LABEL"))
            wkt = esri_geometry_to_wkt(feature.get("geometry"))
            if label is None or wkt is None or rec.get("OBJECTID") is None:
                continue
            # "Brooklyn and College Hill HCD", "Riverslea Estate Landscape CHL":
            # the trailing CHL is the City's filing suffix, not the name.
            if label.endswith(" CHL"):
                label = label[: -len(" CHL")].strip()
            out["id"].append(f"glp-chl-{int(rec['OBJECTID'])}")
            out["name"].append(label)
            out["wkt"].append(wkt)
            out["address"].append(None)
            out["type"].append(clean(rec.get("STAT")))
    return out


def build() -> pa.Table:
    parts = [properties(), landscapes()]
    cols = {k: [v for part in parts for v in part[k]] for k in parts[0]}
    n = len(cols["id"])
    if n == 0:
        raise RuntimeError("Guelph heritage register returned no rows")
    return pa.table(
        {
            "landmark_id": pa.array(cols["id"], type=pa.string()),
            "city": pa.array(["CAGLP"] * n, type=pa.string()),
            "name": pa.array(cols["name"], type=pa.string()),
            "geometry_raw": pa.array(cols["wkt"], type=pa.string()),
            "address": pa.array(cols["address"], type=pa.string()),
            "property_type": pa.array(cols["type"], type=pa.string()),
        }
    )


if __name__ == "__main__":
    emit(build())
