#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Barcelona's cultural points of interest, from Open Data BCN (CKAN).

Source: "Punts d'interès cultural de la ciutat de Barcelona"
(`punts-informacio-turistica`), the Ajuntament's register of the city's main
cultural sites -- monuments, churches, museums, theatres, parks and gardens,
modernista buildings, air-raid shelters -- maintained in the city's central
tourism database and published under CC BY 4.0 on the same portal as the
trees.  Resource `31431b23`, datastore active: 732 rows on 2026-11-22, one per
site, every one named, positioned and in Barcelona.  The register has no
category column; a few of its entries are historic establishments (Bar
Marsella, Hotel Casa Fuster, Pastisseria Escribà) and neighbourhoods (El
Raval), which are kept: they are the city's own choice of cultural sites.

This is the runbook's second preference (a heritage dataset on the trees' own
portal).  The first preference, the city's *Catàleg del patrimoni
arquitectònic protegit* (`patrimoni-arquitectonic-protegit`, also CC BY 4.0),
is published only as SHP/DGN/WMS file downloads, and the portal answers every
file download with 403 outside its allow list; only the API is reachable.

**Keyed on `register_id`**, the tourism database's record number (732
distinct over 732 rows).  The export writes a UTF-8 byte-order mark into the
first column's values (`\\ufeff92086003129`), which is stripped.  Prefixed
`bcn-pic-`.
"""

import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit
from shared.platforms.ckan import CkanResource, iter_datastore_rows

RESOURCE = CkanResource(
    "opendata-ajuntament.barcelona.cat/data", "31431b23-d5b9-42b8-bcd0-a84da9d8c7fa"
)
FIELDS = "register_id,name,geo_epgs_4326_lat,geo_epgs_4326_lon"


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).replace("﻿", "").split())
    return text or None


def as_float(value) -> float | None:
    text = clean(value)
    if text is None:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def read_points() -> list[dict]:
    records: list[dict] = []
    seen: set[str] = set()
    unusable = 0
    for page in iter_datastore_rows(RESOURCE, fields=FIELDS, sort="_id"):
        for rec in page:
            key = clean(rec.get("register_id"))
            name = clean(rec.get("name"))
            lat = as_float(rec.get("geo_epgs_4326_lat"))
            lon = as_float(rec.get("geo_epgs_4326_lon"))
            if not key or name is None or lat is None or lon is None or key in seen:
                unusable += 1
                continue
            seen.add(key)
            records.append(
                {
                    "landmark_id": f"bcn-pic-{key}",
                    "name": name,
                    "geometry_raw": f"POINT ({lon} {lat})",
                }
            )
    print(
        f"Barcelona cultural points: {len(records)} kept, {unusable} dropped "
        "(no id, name or position, or a repeated id)",
        file=sys.stderr,
    )
    return records


def transform(records: list[dict]) -> pa.Table:
    return pa.table(
        {
            "landmark_id": pa.array([r["landmark_id"] for r in records], type=pa.string()),
            "city": pa.array(["ESBCN"] * len(records), type=pa.string()),
            "name": pa.array([r["name"] for r in records], type=pa.string()),
            "geometry_raw": pa.array([r["geometry_raw"] for r in records], type=pa.string()),
        }
    )


if __name__ == "__main__":
    emit(transform(read_points()))
