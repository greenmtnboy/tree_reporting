#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Utrecht's monument registers (rijks- and gemeentelijke monumenten).

The later of the two layers' MAX(`DDMUTATIE`), the register's own
last-mutation timestamp per monument, read as one sorted row from each
layer of the city's GeoServer (2024-06-07 for the municipal register when
this was written).  A missing or unreadable column raises (the register
changed shape); a transport failure degrades to the epoch through
`emit_freshness`.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit_freshness
from shared.platforms.wfs import WfsLayer, wfs_max_property

BASE_URL = "https://geodata.utrecht.nl/geoserver/UtrechtOpen/wfs"
LAYERS = (
    WfsLayer(BASE_URL, "UtrechtOpen:RIJKSMONUMENTEN", timeout=60),
    WfsLayer(BASE_URL, "UtrechtOpen:GEMEENTELIJKE_MONUMENTEN", timeout=60),
)


def fetch_modified_at() -> datetime:
    return max(wfs_max_property(layer, "DDMUTATIE") for layer in LAYERS)


if __name__ == "__main__":
    emit_freshness("NLUTC", fetch_modified_at, label="NLUTC landmarks")
