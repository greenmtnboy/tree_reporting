#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Geneva's classified monuments (SITG `DPS_CLASSEMENT`).

The newest decree date over the register: `DATE_ACE`, the Conseil d'Etat
decree as a `dd.mm.yyyy` string, so it is read from the rows (1,016, one or
two requests) rather than with an `outStatistics` MAX, which would compare
the strings.  A classification is the register's edit, so a new decree moves
it.  Not the layer's `editingInfo` (it has none) nor the SITG catalogue's
`dateModified`, which the catalogue regenerates every day.

A register with no readable date raises (it changed shape); a transport
failure degrades to the epoch through `emit_freshness`.
"""

import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit_freshness
from shared.platforms.arcgis import FeatureLayer, iter_attributes

REGISTER = FeatureLayer(
    "https://vector.sitg.ge.ch/arcgis/rest/services/DPS_CLASSEMENT/FeatureServer/0",
    timeout=60,
)
_DATE = re.compile(r"^\s*(\d{2})\.(\d{2})\.(\d{4})\s*$")


def fetch_modified_at() -> datetime:
    now = datetime.now(timezone.utc)
    stamps = []
    for page in iter_attributes(REGISTER, out_fields="OBJECTID,DATE_ACE"):
        for attrs in page:
            match = _DATE.match(attrs.get("DATE_ACE") or "")
            if match:
                day, month, year = (int(g) for g in match.groups())
                stamp = datetime(year, month, day, tzinfo=timezone.utc)
                if stamp <= now:
                    stamps.append(stamp)
    if not stamps:
        raise RuntimeError("DPS_CLASSEMENT: no readable DATE_ACE in the register")
    return max(stamps)


if __name__ == "__main__":
    emit_freshness("CHGVA", fetch_modified_at, label="CHGVA landmarks")
