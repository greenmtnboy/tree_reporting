#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Milan's parks and green spaces.

Source: "Spazi freschi: parchi ed aree verdi" (DS3018) on
dati.comune.milano.it, CC BY 4.0: 67 of the city's parks and gardens, each a
named point (`Parco Sempione`, `Giardini Indro Montanelli`) with its municipio.
The same portal as the monumental-tree register, and for a tree map the parks
are what the trees are near: the inventory is a park and street inventory.

There is no id column, so the key is the park's name, slugified; the ingest
refuses a duplicate rather than invent a suffix.
"""

import re
import sys
import unicodedata
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit
from shared.platforms.ckan import CkanResource, read_geojson_features

RESOURCE = CkanResource(
    "dati.comune.milano.it", "66f1237d-e456-4432-9cdc-4f1384a15069"
)


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def slugify(value: str) -> str:
    stripped = "".join(
        c for c in unicodedata.normalize("NFD", value) if not unicodedata.combining(c)
    )
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", stripped.lower())).strip("-")


def transform(features: list[dict]) -> pa.Table:
    records = []
    seen: set[str] = set()
    dropped = 0
    for feature in features:
        props = feature.get("properties") or {}
        name = clean(props.get("Nome Parco"))
        coords = (feature.get("geometry") or {}).get("coordinates") or []
        if name is None or len(coords) < 2 or coords[0] is None or coords[1] is None:
            dropped += 1
            continue
        key = slugify(name)
        if key in seen:
            raise RuntimeError(f"Milan parks: two parks are named {name!r}; the name is the key")
        seen.add(key)
        records.append(
            {
                "landmark_id": f"mil-park-{key}",
                "name": name,
                "geometry_raw": f"POINT({float(coords[0])} {float(coords[1])})",
                "category": "Park",
            }
        )
    print(f"Milan parks: {len(records)} kept, {dropped} dropped (no name or point)", file=sys.stderr)
    return pa.table(
        {
            "landmark_id": pa.array([r["landmark_id"] for r in records], type=pa.string()),
            "city": pa.array(["ITMIL"] * len(records), type=pa.string()),
            "name": pa.array([r["name"] for r in records], type=pa.string()),
            "geometry_raw": pa.array([r["geometry_raw"] for r in records], type=pa.string()),
            "category": pa.array([r["category"] for r in records], type=pa.string()),
        }
    )


if __name__ == "__main__":
    emit(transform(read_geojson_features(RESOURCE)))
