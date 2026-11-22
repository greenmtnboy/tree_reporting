#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Barcelona's Biodiversity Atlas tree layer.

The ingest reads three datastores (geometries, geometry-species relation,
species table); see barcelona_tree_info.py.  The watermark is the latest of
each resource's `last_modified` and each package's `metadata_modified`:

* the resources were last uploaded 2025-02-25 (the atlas's publication);
  a re-upload moves `last_modified`;
* `metadata_modified` (2025-09-23 on all three) is included as well, unlike
  `shared.platforms.ckan.data_last_modified`, because this portal reloads
  other datastores *in place* without touching the resource stamp (the
  inventory datasets' `last_modified` has read 2024-11-12 for two years
  while their rows changed weekly), and a package edit is the only other
  signal it gives.  It moves a few times a year at most, and a spurious
  rebuild of one city is cheap next to a city frozen for ever.

A response without the stamps raises (schema change); an outage degrades
through `emit_freshness`.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit_freshness
from shared.platforms.ckan import CkanResource, package_metadata, resource_metadata

PORTAL = "opendata-ajuntament.barcelona.cat/data"
RESOURCES = (
    CkanResource(PORTAL, "7c64504a-a718-48fe-b466-8c93a160b0dd"),  # geometries
    CkanResource(PORTAL, "506c3c5d-8f9b-409d-ae96-c0912da4a505"),  # geometry -> species
    CkanResource(PORTAL, "c654c488-2822-4e51-ad9b-f443989a4b41"),  # species table
)


def _parse(value) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    # CKAN stamps are naive UTC.
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def fetch_modified_at() -> datetime:
    now = datetime.now(timezone.utc)
    stamps: list[datetime] = []
    for resource in RESOURCES:
        uploaded = _parse(resource_metadata(resource).get("last_modified"))
        edited = _parse(package_metadata(resource).get("metadata_modified"))
        if uploaded is None or edited is None:
            raise RuntimeError(
                f"Barcelona atlas resource {resource.resource_id}: no "
                "last_modified or no package metadata_modified -- has the "
                "portal changed its metadata shape?"
            )
        stamps.extend((min(uploaded, now), min(edited, now)))
    return max(stamps)


if __name__ == "__main__":
    emit_freshness("ESBCN", fetch_modified_at)
