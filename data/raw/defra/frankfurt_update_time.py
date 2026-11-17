#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Frankfurt's tree cadastre.

The source is a CSV with no edit-date column, served with no `Last-Modified`
(only `Content-Disposition: ...; size=<bytes>`), so the watermark is the
portal's DCAT record for the dataset, offenedaten.frankfurt.de's JSON-LD
export of "Baumkataster OP": the latest `modified` of the dataset and its CSV
distribution (`YYYY-MM-DD`, read as midnight UTC).

What that costs: it is the catalogue's stamp, not the data's.  It read
2025-02-06 on 2026-11-16 while the file carried 1,441 trees planted in 2026 and
3,326 more rows than Esri's February-2025 copy, so it does **not** move when
the city regenerates the file.  The city still rebuilds weekly:
`defra_published_data_updated_through` is the greatest of this, the community
probe and the OSM staging probe, and the weekly OSM extract re-stamps its
staging object every run -- Cologne's and Vienna's arrangement.

A record without the dataset node or a readable `modified` raises -- the
portal changing shape -- and a transport failure or a non-JSON answer degrades
to the epoch through `emit_freshness`.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit_freshness, get_json_with_retry

DATASET_URI = "https://offenedaten.frankfurt.de/dcat/dataset/de-he-frankfurtam-baumkataster_op"
METADATA_URL = f"{DATASET_URI}.jsonld"
DISTRIBUTION_URI = f"{DATASET_URI}#dist-csv"


def _parse(value) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        stamp = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc)


def fetch_modified_at() -> datetime:
    payload = get_json_with_retry(METADATA_URL, timeout=60)
    graph = payload.get("@graph") if isinstance(payload, dict) else None
    if not isinstance(graph, list):
        raise RuntimeError(f"{METADATA_URL}: no @graph in the DCAT record")
    nodes = {node.get("@id"): node for node in graph if isinstance(node, dict)}
    dataset = nodes.get(DATASET_URI)
    if dataset is None:
        raise RuntimeError(f"{METADATA_URL}: the record has no {DATASET_URI} node")
    stamps = [_parse(dataset.get("modified"))]
    distribution = nodes.get(DISTRIBUTION_URI)
    if distribution is not None:
        stamps.append(_parse(distribution.get("modified")))
    stamps = [s for s in stamps if s is not None]
    if not stamps:
        raise RuntimeError(
            f"{METADATA_URL}: unreadable modified={dataset.get('modified')!r}"
        )
    return max(stamps)


if __name__ == "__main__":
    emit_freshness("DEFRA", fetch_modified_at)
