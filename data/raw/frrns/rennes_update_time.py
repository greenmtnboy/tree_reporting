#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Rennes Métropole's tree register.

`MAX(date_maj)` -- each row's last-edit date, which the layer declares
non-nullable -- read as one feature sorted descending from the GeoServer WFS
(`espub_esv:arbre`, see `rennes_tree_info.py`), so the probe costs one small
request against a 192k-row layer.  The communes edit continuously (the newest
stamp was the day of reading when this was written).  Not the Opendatasoft
catalogue's `modified`: it has been frozen at 2024-11-14 while the layer kept
changing.  The blind spot is a deletion, which moves no row's stamp; a felled
tree is flagged `abattu` and re-stamped rather than deleted, so it is seen.

`date_maj` is a bare date (`2026-11-17Z`), read as midnight UTC.
`wfs_max_property` raises when the column is missing or empty -- the register
changing shape -- and lets a transport failure degrade to the epoch through
`emit_freshness`, so Rennes sits out that run.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit_freshness
from shared.platforms.wfs import WfsLayer, wfs_max_property

# Kept in step with `rennes_tree_info.py`.
LAYER = WfsLayer("https://public.sig.rennesmetropole.fr/geoserver/ows", "espub_esv:arbre")


def fetch_modified_at() -> datetime:
    return wfs_max_property(LAYER, "date_maj")


if __name__ == "__main__":
    emit_freshness("FRRNS", fetch_modified_at)
