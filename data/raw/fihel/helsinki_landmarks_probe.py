#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Helsinki's protected buildings and natural monuments.

The buildings layer carries a real edit stamp, `paivitetty` ("updated",
`06.06.2018`), per row -- unlike its `paivitetty_tietopalveluun`, which is the
nightly load date and would rebuild the city's landmarks every week for
nothing.  It is a `dd.mm.yyyy` string, which GeoServer cannot sort as a date,
so the probe reads that one column for every row (a few hundred kilobytes)
and takes the maximum here.  The natural monuments carry only the load date
and are 31 rows that change on a scale of decades; they ride the buildings'
watermark.

A layer with no readable `paivitetty` on any row raises rather than
degrading: that is the register changing shape, not an outage.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import emit_freshness
from _wfs_shared import WfsLayer, iter_wfs_features

BUILDINGS = WfsLayer(
    "https://kartta.hel.fi/ws/geoserver/avoindata/wfs",
    "avoindata:Asemakaavoissa_suojellut_rakennukset_alue",
)


def parse_finnish_date(value) -> datetime | None:
    text = " ".join(str(value or "").split())
    if not text:
        return None
    try:
        return datetime.strptime(text[:10], "%d.%m.%Y").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def fetch_modified_at() -> datetime:
    stamps: list[datetime] = []
    for page in iter_wfs_features(BUILDINGS, sort_by="id", properties=["id", "paivitetty", "muokattu"], page_size=10000):
        for feature in page:
            props = feature.get("properties") or {}
            for column in ("paivitetty", "muokattu"):
                stamp = parse_finnish_date(props.get(column))
                if stamp is not None:
                    stamps.append(stamp)
    if not stamps:
        raise RuntimeError("no readable paivitetty/muokattu date on any protected building")
    return max(stamps)


if __name__ == "__main__":
    emit_freshness("FIHEL", fetch_modified_at, label="FIHEL landmarks")
