#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Perth's heritage places: the City of Perth Heritage List (City Planning
Scheme No. 2), from the city's ArcGIS Online.

Source: "City of Perth Heritage List CPS2 PV" (item
550909775e984aa19d5b4fadcce4b3e6, owner GIS.SERVICES_COP, the same
organisation that publishes the trees), layer 1 "Heritage Places View".  It
is the city's statutory heritage list -- the places protected under its
planning scheme -- and records for each whether it is also on the State
Register of Heritage Places (`SHO_LISTED`).  The item is public but states no
licence; the city's open data catalogue publishes its other layers CC BY 4.0.

The State Register itself (DPLH-006 on data.wa.gov.au) was not used: SLIP
serves it only behind a login under a "Custom (Active Acceptance)" licence,
and every State-registered place inside the city is on this list anyway.

This is the first preference in the runbook's landmark source order (an
official designation registry, on the same portal as the trees), so nothing
is geocoded or staged; the probe is the layer's own edit date.

Measured 2026-11-16 (layer `dataLastEditDate` 2026-02-17): 410 polygons, one
per `HER_ID`.  15 whose `PLACE_STATUS` is Demolished or Removed are left out
(the list keeps them as a record), leaving **395 places**: 384 places, 7
precincts, 3 heritage areas and 1 group.  Precincts are kept even though a few
contain listed places of their own: they are separately listed, and the agent
is asked about "the Perth Cultural Centre" as often as about one building in
it.

Field mapping:

  HER_ID                         -> landmark_id ("per-h{HER_ID}") and
                                    register_number
  CURR_PLACE_NAME                -> name; when the same text names more than
                                    one place ("Commercial Building" names 24)
                                    the address is appended
  CURR_STREET_NUM/NAME, SUBURB   -> address
  SHO_LISTED                     -> protection_type ("State Register" or
                                    "City of Perth Heritage List")
  HERITAGE_PLACE_TYPE            -> category
  CONSTRUCTION_YEAR_LB1          -> year_built (earliest construction year)
  DATE_LISTED                    -> designation_date
  polygon geometry (outSR 4326)  -> geometry_raw (WKT)
"""

import sys
from collections import Counter
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

# Kept in step with perth_landmarks_probe.py.
LAYER = FeatureLayer(
    "https://services7.arcgis.com/v8XBa2naYNQGOjlG/arcgis/rest/services/"
    "City_of_Perth_Heritage_List_CPS2/FeatureServer/1"
)

OUT_FIELDS = (
    "OBJECTID,HER_ID,CURR_PLACE_NAME,HIST_PLACE_NAME,CURR_STREET_NUM,"
    "CURR_STREET_NAME,CURR_SUBURB,PLACE_STATUS,SHO_LISTED,HERITAGE_PLACE_TYPE,"
    "CONSTRUCTION_YEAR_LB1,DATE_LISTED,RETIRED"
)

GONE_STATUSES = frozenset({"demolished", "removed"})


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def address_of(attrs: dict) -> str | None:
    street = " ".join(
        part
        for part in (clean(attrs.get("CURR_STREET_NUM")), clean(attrs.get("CURR_STREET_NAME")))
        if part
    )
    suburb = clean(attrs.get("CURR_SUBURB"))
    parts = [p for p in (street, suburb) if p]
    return ", ".join(parts) or None


def keep(attrs: dict) -> bool:
    if attrs.get("RETIRED") is not None:
        return False
    status = (clean(attrs.get("PLACE_STATUS")) or "").lower()
    return status not in GONE_STATUSES


def transform(features: list[dict]) -> pa.Table:
    rows = []
    for feature in features:
        attrs = feature.get("attributes") or {}
        her_id = clean(attrs.get("HER_ID"))
        if her_id is None or not keep(attrs):
            continue
        wkt = esri_geometry_to_wkt(feature.get("geometry"))
        label = clean(attrs.get("CURR_PLACE_NAME")) or clean(attrs.get("HIST_PLACE_NAME"))
        if wkt is None or label is None:
            continue
        rows.append((her_id, label, wkt, attrs))

    if not rows:
        raise RuntimeError("City of Perth heritage list returned no current places")

    repeats = Counter(label for _, label, _, _ in rows)

    landmark_id, name, geometry_raw = [], [], []
    address, protection_type, register_number, category = [], [], [], []
    year_built, designation_date = [], []
    for her_id, label, wkt, attrs in rows:
        addr = address_of(attrs)
        if repeats[label] > 1 and addr:
            label = f"{label}, {addr}"
        year = attrs.get("CONSTRUCTION_YEAR_LB1")

        landmark_id.append(f"per-h{her_id}")
        name.append(label)
        geometry_raw.append(wkt)
        address.append(addr)
        protection_type.append(
            "State Register"
            if (clean(attrs.get("SHO_LISTED")) or "").lower() == "yes"
            else "City of Perth Heritage List"
        )
        register_number.append(her_id)
        category.append(clean(attrs.get("HERITAGE_PLACE_TYPE")))
        year_built.append(int(year) if year else None)
        designation_date.append(esri_ms_to_date(attrs.get("DATE_LISTED")))

    return pa.table(
        {
            "landmark_id": pa.array(landmark_id, type=pa.string()),
            "city": pa.array(["AUPER"] * len(landmark_id), type=pa.string()),
            "name": pa.array(name, type=pa.string()),
            "geometry_raw": pa.array(geometry_raw, type=pa.string()),
            "address": pa.array(address, type=pa.string()),
            "protection_type": pa.array(protection_type, type=pa.string()),
            "register_number": pa.array(register_number, type=pa.string()),
            "category": pa.array(category, type=pa.string()),
            "year_built": pa.array(year_built, type=pa.int64()),
            "designation_date": pa.array(designation_date, type=pa.date32()),
        }
    )


if __name__ == "__main__":
    features: list[dict] = []
    for page in iter_features(
        LAYER, out_fields=OUT_FIELDS, return_geometry=True, order_by="OBJECTID"
    ):
        features.extend(page)
    table = transform(features)
    print(
        f"Perth landmarks: {table.num_rows} of {len(features)} listed places",
        file=sys.stderr,
    )
    emit(table)
