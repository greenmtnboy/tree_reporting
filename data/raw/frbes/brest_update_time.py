#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Brest's tree inventory.

The ingest reads `GPB_ESP_Intra_EV_Arbres/MapServer/1411070`, an export of
the Espaces Verts' tree system (Ginkgo) that publishes no `editingInfo` and
no date column, so there is nothing on the layer itself to read.  Two stamps
stand in, and the later is emitted:

  * the data.gouv.fr record for the dataset (`les-arbres`): the later of its
    `last_update` and its ArcGIS resource's `last_modified` (2026-06-20 on
    2026-11-20).  It is the catalogue's stamp, refreshed when the Métropole's
    catalogue republishes the record, not when a tree is edited.
  * the edit stamps of *Arbres plantés* (`GPB_ESP/MapServer/1411014`), the
    Métropole's layer of new plantings, whose rows are reported into the
    inventory (its `GINKO` column, `IDARB` `PA4487` in both): MAX of
    `LAST_EDITED_DATE` and `CREATED_DATE` (2026-11-04 on 2026-11-20).  A
    planting season moves it; a removal or a re-survey of an older tree does
    not.  Esri stores these without a zone; read as UTC they may be an hour
    or two off Paris time, which does not matter at a daily tick.

What this misses -- an edit to an established tree with neither stamp moving
-- is caught by the weekly OSM extract, which re-stamps the city's staging
parquet and so rebuilds the city through `frbes_published_data_updated_through`.

A catalogue answer without the stamps, or a planting layer that no longer
has the columns, raises (a schema change); a transport failure degrades to
the epoch through `emit_freshness`.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit_freshness, get_json_with_retry  # noqa: E402
from shared.platforms.arcgis import FeatureLayer, field_max  # noqa: E402

METADATA_URL = "https://www.data.gouv.fr/api/1/datasets/les-arbres/"
# The resource data.gouv.fr lists for the layer the ingest reads.
RESOURCE_URL = (
    "https://geo.brest-metropole.fr/arcgis/rest/services/public/"
    "GPB_ESP_Intra_EV_Arbres/MapServer/1411070"
)
PLANTINGS = FeatureLayer(
    "https://geo.brest-metropole.fr/arcgis/rest/services/public/GPB_ESP/MapServer/1411014",
    timeout=60,
)


def _parse(value) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp


def catalogue_modified_at() -> datetime:
    payload = get_json_with_retry(METADATA_URL, timeout=60)
    if not isinstance(payload, dict):
        raise RuntimeError(f"{METADATA_URL}: not a dataset record")
    stamps = [_parse(payload.get("last_update"))]
    for resource in payload.get("resources") or []:
        if str(resource.get("url") or "").rstrip("/") == RESOURCE_URL:
            stamps.append(_parse(resource.get("last_modified")))
    stamps = [s for s in stamps if s is not None]
    if not stamps:
        raise RuntimeError(
            f"{METADATA_URL}: cannot read `last_update` ({payload.get('last_update')!r}) "
            "or the ArcGIS resource's `last_modified`"
        )
    return max(stamps)


def fetch_modified_at() -> datetime:
    return max(
        catalogue_modified_at(),
        field_max(PLANTINGS, "LAST_EDITED_DATE"),
        field_max(PLANTINGS, "CREATED_DATE"),
    )


if __name__ == "__main__":
    emit_freshness("FRBES", fetch_modified_at)
