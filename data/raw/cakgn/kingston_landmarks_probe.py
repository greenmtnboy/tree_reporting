#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Freshness probe for Kingston's designated heritage sites.

**The maximum of two stamps.**  The layer publishes no `editingInfo`, so the
usual watermark is gone; what it does carry is `OHA_DESIGNATION`, the date
each property was designated, whose maximum moves whenever the city designates
a new one.  That misses a correction to an existing row, so it is taken
together with the Hub catalogue's `modified` stamp and the later of the two
wins -- the rule `_ckan_shared.data_last_modified` arrived at, and the only
one that cannot freeze a city when one of its inputs stops moving.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from _arcgis_shared import FeatureLayer, field_max, hub_last_modified
from _ingest_shared import UpstreamUnavailable, emit_freshness

HUB_HOST = "opendatakingston.cityofkingston.ca"
LAYER_URL = (
    "https://api.cityofkingston.ca/gis_unfed/rest/services/Planning/"
    "DesignatedHeritageSite/FeatureServer/4"
)
LAYER = FeatureLayer(LAYER_URL)


def fetch_modified_at() -> datetime:
    """The later of the newest designation and the catalogue's own stamp.

    Only an *availability* failure is tolerated on either read, so a portal
    that is briefly down still yields the other stamp while a mapping that has
    gone wrong still raises.  Degrading a schema error into "no new data"
    would freeze this city's landmark parquet silently and for ever.
    """
    stamps: list[datetime] = []
    errors: list[Exception] = []
    for read in (lambda: field_max(LAYER, "OHA_DESIGNATION"),
                 lambda: hub_last_modified(HUB_HOST, LAYER_URL)):
        try:
            stamps.append(read())
        except UpstreamUnavailable as exc:
            errors.append(exc)
    if not stamps:
        raise errors[0]
    return max(stamps)


if __name__ == "__main__":
    emit_freshness("CAKGN", fetch_modified_at, label="CAKGN landmarks")
