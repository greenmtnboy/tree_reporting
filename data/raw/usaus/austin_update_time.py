#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Austin's tree inventory.

Reads `rowsUpdatedAt` off the Tree Inventory's (`wrik-xasw`) view metadata --
one small JSON response -- so a refresh only re-downloads the 62k rows when
the city has actually republished them.  The inventory is a March 2020
compilation and its rows have not moved since 2020-03-13, so in practice this
keeps Austin's municipal partition out of every run until the city replaces
it.

`rows_updated_at` raises rather than degrading when the field is absent: that
is the portal changing its metadata shape, not the portal being down, and
`emit_freshness` must not turn it into "no new data".  A genuine outage is
caught by `get_json_with_retry` inside it and does degrade to the epoch.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit_freshness
from shared.platforms.socrata import SocrataDataset, rows_updated_at

DATASET = SocrataDataset("data.austintexas.gov", "wrik-xasw")


def fetch_modified_at() -> datetime:
    return rows_updated_at(DATASET)


if __name__ == "__main__":
    emit_freshness("USAUS", fetch_modified_at)
