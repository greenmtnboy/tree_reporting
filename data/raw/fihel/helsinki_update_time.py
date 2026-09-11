#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Helsinki's tree register.

`MAX(paivitetty_tietopalveluun)` -- "updated to the data service", the date
the register was last loaded into the open WFS -- read as one feature sorted
descending.  It is a load stamp rather than an edit stamp: every row carries
the same date, and it moves when the city republishes the layer, which it
appears to do nightly (every row read 2026-09-10 on 2026-09-11).  So the city
rebuilds on each tick of its twice-weekly cron; at 66k rows that is the cheap
side of the trade, and the alternatives are worse -- the HRI catalogue entry's
`metadata_modified` is from 2024 and the register itself says it is not
updated systematically.

`wfs_max_property` raises when the column is missing or empty (the register
changing shape) and lets a transport failure degrade to the epoch through
`emit_freshness`, so Helsinki sits out that run.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import emit_freshness
from _wfs_shared import WfsLayer, wfs_max_property

# Kept in step with `helsinki_tree_info.py` by `tests/test_city_wiring.py`.
LAYER = WfsLayer("https://kartta.hel.fi/ws/geoserver/avoindata/wfs", "avoindata:Puurekisteri_piste")


def fetch_modified_at() -> datetime:
    return wfs_max_property(LAYER, "paivitetty_tietopalveluun")


if __name__ == "__main__":
    emit_freshness("FIHEL", fetch_modified_at)
