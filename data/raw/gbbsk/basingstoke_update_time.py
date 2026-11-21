#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Basingstoke and Deane's tree layer.

The layer publishes no `editingInfo` and has no date field: it is reloaded
whole every night from the council's Ezytreev system, so a load stamp would
move daily whatever happened.  What moves when the inventory does is the
newest inspection, recorded as text in `LastVisit` (`Last Visit 25/06/2026
by RS  for Inspection`, day first).  The probe counts the rows whose
`LastVisit` falls in each month, walking back from the current one, and
reads the dates of the first month that has any (a few hundred rows): the
latest of them is the stamp.  It was 2026-11-19 when the city was wired.
Inspections happen most working days, so the city rebuilds at most of its
ticks; that is a 72k-row download, and there is no coarser signal to watch.

Every request goes through `get_json_with_retry`, so an outage degrades to
the epoch; no readable visit in the last three years raises, since that
means `LastVisit` was renamed or reworded.
"""

import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, feature_count, iter_attributes
from shared.ingest import emit_freshness

LAYER = FeatureLayer(
    "https://gis.basingstoke.gov.uk/server/rest/services/AGOL/TreesInspections/FeatureServer/6",
    timeout=60,
)

MONTHS_BACK = 36

_VISIT = re.compile(r"Last Visit (\d{2})/(\d{2})/(\d{4})\b")


def month_where(year: int, month: int) -> str:
    return f"LastVisit LIKE 'Last Visit __/{month:02d}/{year} %'"


def fetch_modified_at() -> datetime:
    now = datetime.now(timezone.utc)
    year, month = now.year, now.month
    for _ in range(MONTHS_BACK):
        where = month_where(year, month)
        if feature_count(LAYER, where=where):
            stamps = []
            for page in iter_attributes(LAYER, out_fields="OBJECTID,LastVisit", where=where):
                for rec in page:
                    match = _VISIT.match(rec.get("LastVisit") or "")
                    if match:
                        day, mon, yr = (int(g) for g in match.groups())
                        stamps.append(datetime(yr, mon, day, tzinfo=timezone.utc))
            if not stamps:
                raise RuntimeError(f"no readable LastVisit date among rows matching {where!r}")
            return max(stamps)
        year, month = (year, month - 1) if month > 1 else (year - 1, 12)
    raise RuntimeError(f"no LastVisit in the last {MONTHS_BACK} months on {LAYER.base}")


if __name__ == "__main__":
    emit_freshness("GBBSK", fetch_modified_at)
