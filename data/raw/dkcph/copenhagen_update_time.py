#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Copenhagen's tree register.

`MAX(opdateret_dato)` -- the register's own per-row update stamp -- read as
one feature sorted descending, so the probe costs one small request against a
40 MB layer.  The register is edited continuously (the stamp read today when
this was written), so the city rebuilds on most ticks of its twice-weekly
cron; at 68k rows that is the cheap side of the trade, and the alternative
watermarks are worse: the WFS publishes no layer-level stamp and the Danish
open-data catalogue's record of the dataset is a description, not the data.

`wfs_max_property` raises when the column is missing or empty -- the register
changing shape, not an outage -- and lets a transport failure degrade to the
epoch through `emit_freshness`, so Copenhagen sits out that run.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import emit_freshness
from _wfs_shared import WfsLayer, wfs_max_property

# Kept in step with `copenhagen_tree_info.py` by `tests/test_city_wiring.py`.
LAYER = WfsLayer("https://wfs-kbhkort.kk.dk/k101/ows", "k101:trae_basis")


def fetch_modified_at() -> datetime:
    return wfs_max_property(LAYER, "opdateret_dato")


if __name__ == "__main__":
    emit_freshness("DKCPH", fetch_modified_at)
