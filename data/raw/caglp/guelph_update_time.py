#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Guelph's (Ontario) tree inventory.

The "Trees" layer (`OpenData/OpenData1/FeatureServer/7` on the City's own
ArcGIS Server) publishes no `editingInfo` and has no edit-date column, so two
weaker stamps are read and the **maximum** taken (the rule
`hub_last_modified` documents -- the only combination that cannot freeze the
city):

* the Guelph GeoDataHub's DCAT `modified` for the dataset (explore.guelph.ca),
  which follows the City's republish of the layer (2026-11-20 09:16Z when the
  city was wired, while untouched datasets on the same hub sit at 2024-09);
* MAX(`DT_PLANTED`), which moves whenever forestry records a new planting
  (2026-10-29 at wiring), in case the hub stamp stops following the data.

Either read may be briefly unavailable and the other still answers; a schema
question on either (the dataset gone from the feed, the field gone) raises,
and only when both are unavailable does the probe degrade to the epoch.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, field_max, hub_last_modified
from shared.ingest import UpstreamUnavailable, emit_freshness

LAYER = FeatureLayer(
    "https://gismaps.guelph.ca/hosting/rest/services/OpenData/OpenData1/"
    "FeatureServer/7",
    timeout=120,
)
HUB_HOST = "explore.guelph.ca"


def fetch_modified_at() -> datetime:
    """The later of the hub's stamp and the newest planting date."""
    stamps: list[datetime] = []
    errors: list[Exception] = []
    for read in (
        lambda: hub_last_modified(HUB_HOST, LAYER.base, timeout=LAYER.timeout),
        lambda: field_max(LAYER, "DT_PLANTED"),
    ):
        try:
            stamps.append(read())
        except UpstreamUnavailable as exc:
            errors.append(exc)
    if not stamps:
        raise errors[0]
    return max(stamps)


if __name__ == "__main__":
    emit_freshness("CAGLP", fetch_modified_at)
