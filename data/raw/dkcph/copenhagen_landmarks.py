#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Copenhagen's public monuments, from the municipality's WFS.

Source: `k101:monumenter` on `wfs-kbhkort.kk.dk`, the same GeoServer the
trees come from -- the city's inventory of the statues, memorials and
sculptures it maintains in public space, 424 points, each named
(`Reformationsmonumentet`, `Den lille Havfrue`, `Mødet`).  CC BY 4.0, the
same terms as the tree register.

This is the runbook's second preference (the same portal as the trees) and it
was chosen over the first-preference registry on purpose.  The city also
publishes its listed buildings -- `fredede_bygninger_save`, 1,731 protected
under the national act among 63,497 surveyed -- but that layer names a
building by its address alone (`Stockholmsgade 20`), which is a designation
rather than a landmark to anyone asking what is near a tree.  The monuments
are what people call the places by.

**Keyed on the feature id.**  The layer's own `id`, `loebenummer` and `web_id`
are each null on some rows and shared on others (`id` covers 385 distinct
values over 424 rows), so none of them is a key.  GeoServer's feature id is
the table's primary key rendered as `monumenter.<n>` and is stable across
requests; the number after the dot is what this publishes.  22 rows carry no
name and are dropped.
"""

import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import emit
from _wfs_shared import WfsLayer, geojson_to_wkt, iter_wfs_features

LAYER = WfsLayer("https://wfs-kbhkort.kk.dk/k101/ows", "k101:monumenter")
PROPERTIES = ["navn", "loebenummer", "wkb_geometry"]


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def read_monuments() -> list[dict]:
    records: list[dict] = []
    seen: set[str] = set()
    unusable = 0
    for page in iter_wfs_features(LAYER, sort_by="navn", properties=PROPERTIES):
        for feature in page:
            props = feature.get("properties") or {}
            fid = str(feature.get("id") or "")
            key = fid.rsplit(".", 1)[-1] if "." in fid else None
            name = clean(props.get("navn"))
            wkt = geojson_to_wkt(feature.get("geometry"))
            if not key or name is None or wkt is None or key in seen:
                unusable += 1
                continue
            seen.add(key)
            records.append(
                {
                    "landmark_id": f"cph-mon-{key}",
                    "name": name,
                    "geometry_raw": wkt,
                    "category": "Monument",
                }
            )
    print(
        f"Copenhagen monuments: {len(records)} kept, {unusable} dropped (no name, "
        f"position or feature id)",
        file=sys.stderr,
    )
    return records


def transform(records: list[dict]) -> pa.Table:
    return pa.table(
        {
            "landmark_id": pa.array([r["landmark_id"] for r in records], type=pa.string()),
            "city": pa.array(["DKCPH"] * len(records), type=pa.string()),
            "name": pa.array([r["name"] for r in records], type=pa.string()),
            "geometry_raw": pa.array([r["geometry_raw"] for r in records], type=pa.string()),
            "category": pa.array([r["category"] for r in records], type=pa.string()),
        }
    )


if __name__ == "__main__":
    emit(transform(read_monuments()))
