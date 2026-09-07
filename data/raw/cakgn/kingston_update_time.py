#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Kingston's city-owned tree inventory.

Kingston serves its inventory from an on-prem ArcGIS Server through the AGOL
proxy, and that layer publishes **no `editingInfo`** and carries no edit-date
column -- `RETIRED_DATE` is the only date in the schema, and it records when a
tree was removed rather than when the table was written.  So neither of the
two watermarks the other cities use is available, and this falls back to the
third: the Hub catalogue's own `modified` stamp for the dataset.

`hub_last_modified` documents what that costs.  In short it is the
*catalogue's* stamp rather than the data's, so it can move for a description
edit and -- the dangerous direction -- can fail to move when the publisher
overwrites the service in place.  Two things bound that: the city's cron is
twice weekly regardless, and an approved community submission or a new OSM
extract makes CAKGN's parquet stale through their own probes.  A layer that
gains an `editingInfo` should move to `layer_last_edit`, or take the maximum
of the two the way `_ckan_shared.data_last_modified` does.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from _arcgis_shared import hub_last_modified
from _ingest_shared import emit_freshness

HUB_HOST = "opendatakingston.cityofkingston.ca"
LAYER_URL = (
    "https://utility.arcgis.com/usrsvcs/servers/"
    "511fd5299053486daf48c6466332320c/rest/services/Eng/City_Owned_Trees/"
    "FeatureServer/0"
)


def fetch_modified_at() -> datetime:
    return hub_last_modified(HUB_HOST, LAYER_URL)


if __name__ == "__main__":
    emit_freshness("CAKGN", fetch_modified_at)
