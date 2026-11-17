#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for the Métropole de Lyon's street-tree register.

`MAX(datereleve)` -- the date each tree was last surveyed, the register's own
per-row stamp -- read as one feature sorted descending, so the probe costs one
small request against a 112k-row layer.  The survey runs continuously (the
newest stamp was three days old when this was written), so the city rebuilds
on most ticks of its twice-weekly cron.  The WFS publishes no layer-level
edit stamp and the portal's catalogue record is not machine-readable from
outside its single-page app, so this is the best watermark there is; its
blind spot is a deletion, which moves no survey date and is picked up at the
next survey.

`wfs_max_property` raises when the column is missing or empty -- the register
changing shape -- and lets a transport failure degrade to the epoch through
`emit_freshness`, so Lyon sits out that run.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit_freshness
from shared.platforms.wfs import WfsLayer, wfs_max_property

# Kept in step with `lyon_tree_info.py`.
LAYER = WfsLayer(
    "https://data.grandlyon.com/geoserver/metropole-de-lyon/ows",
    "metropole-de-lyon:abr_arbres_alignement.abrarbre",
)


def fetch_modified_at() -> datetime:
    return wfs_max_property(LAYER, "datereleve")


if __name__ == "__main__":
    emit_freshness("FRLYO", fetch_modified_at)
