#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Austin, TX's designated historic landmarks.

Source: "Historical Landmarks" (`8k49-bptr`) on data.austintexas.gov, the
city's register of locally designated historic landmarks, fed from the
Planning Department's GIS (`austin.maps.arcgis.com` item
`966292112c6046e8aaf3fd7c33c3382e`) by an FME job.  689 points when the city
was wired (2026-11-11), each a building or site with its name, street address
and the historic-zoning case (`C14H-YYYY-NNNN`) that designated it.  An
official register on the trees' own portal is the runbook's first preference,
so there is no Nominatim geocoding, no committed CSV and nothing to stage.

**The key is `landmark_id`**, the register's own number for a designated
property: the case number with a two-digit suffix (`1974002805`).  It is 689
distinct and non-null, and it is stable where `objectid` is not -- the FME
load rewrote every row on 2025-12-30.  `case_number` alone is not unique: the
Moonlight Towers were designated together under `C14H-1974-0028` and are
sixteen rows, one per tower.

`historic_landmark_id` is the city's landmark number and goes to
`landmark_number`.  It is null on 23 rows (all but one designated
2023-2025, not yet numbered) and `0` on 28; both publish as null.
"""

import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit, make_point_wkt
from shared.platforms.socrata import SocrataDataset, iter_rows, point_lon_lat

DATASET = SocrataDataset("data.austintexas.gov", "8k49-bptr")

SELECT = "landmark_id,building_name,address,historic_landmark_id,the_geom"


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def landmark_number(value) -> int | None:
    """The city's landmark number; `0` and blank are unnumbered."""
    text = clean(value)
    if text is None:
        return None
    try:
        number = int(float(text))
    except ValueError:
        raise RuntimeError(f"Austin historic_landmark_id {text!r} is not a number")
    return number or None


def landmark_key(value) -> str | None:
    text = clean(value)
    if text is None:
        return None
    # Socrata serves the number column as text; "1974002805" or "1974002805.0".
    return str(int(float(text)))


def transform(rows: list[dict]) -> pa.Table:
    landmark_id: list[str] = []
    name: list[str] = []
    geometry_raw: list[str] = []
    address: list[str | None] = []
    number: list[int | None] = []
    skipped = 0

    for rec in rows:
        key = landmark_key(rec.get("landmark_id"))
        label = clean(rec.get("building_name"))
        lon, lat = point_lon_lat(rec.get("the_geom"))
        wkt = make_point_wkt(lon, lat)
        if key is None or not label or wkt is None:
            skipped += 1
            continue
        landmark_id.append(f"aus-{key}")
        name.append(label)
        geometry_raw.append(wkt)
        address.append(clean(rec.get("address")))
        number.append(landmark_number(rec.get("historic_landmark_id")))

    if skipped:
        print(f"Austin landmarks: skipped {skipped} row(s) without an id, name or point", file=sys.stderr)
    if len(set(landmark_id)) != len(landmark_id):
        raise RuntimeError("Austin landmark_id repeats; the register's key has changed")

    return pa.table(
        {
            "landmark_id": pa.array(landmark_id, type=pa.string()),
            "name": pa.array(name, type=pa.string()),
            "city": pa.array(["USAUS"] * len(landmark_id), type=pa.string()),
            "geometry_raw": pa.array(geometry_raw, type=pa.string()),
            "address": pa.array(address, type=pa.string()),
            "landmark_number": pa.array(number, type=pa.int64()),
        }
    )


if __name__ == "__main__":
    rows: list[dict] = []
    for page in iter_rows(DATASET, select=SELECT, order="landmark_id"):
        rows.extend(page)
    emit(transform(rows))
