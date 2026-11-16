#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Freshness probe for Ballarat's Victorian Heritage Register places.

The register layer's newest `ufi_created` -- the time Vicmap stamps on a
parcel feature when it is added or re-captured -- read as one row sorted
descending (`wfs_max_property`).  The layer is statewide and carries no
council column, so this watermark moves when Heritage Victoria edits any
place in Victoria, not only Ballarat's; that costs a rebuild of a 67-row
parquet on the weekly landmark lane, which is cheaper than reading the
city boundary here.  On 2026-11-16 it read 2026-10-15T01:59Z statewide;
the newest parcel inside Ballarat is from 2025-02-27.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.wfs import WfsLayer, wfs_max_property
from shared.ingest import emit_freshness

REGISTER = WfsLayer(
    "https://opendata.maps.vic.gov.au/geoserver/wfs",
    "open-data-platform:heritage_register",
)


def fetch_modified_at() -> datetime:
    return wfs_max_property(REGISTER, "ufi_created")


if __name__ == "__main__":
    emit_freshness("AUBAL", fetch_modified_at, label="AUBAL landmarks")
