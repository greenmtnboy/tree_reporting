#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Evanston, Illinois's National Register listings.

**The National Register of Historic Places** (the National Park Service's
`cultural_resources/nrhp_locations` MapServer, points then polygons), read
live, within `CITY_BOUNDS["USEVN"]` and with `City = Evanston` -- the box
also holds listings in Wilmette, Kenilworth, Lincolnwood and Chicago's Rogers
Park.  Listed buildings, sites and structures, keyed `evn-nr-{NRIS_Refnum}`
with `CertDate` as `year_designated`; districts are areas and are not
published.  NPS data is a US federal work.

Evanston's own landmark register (the Preservation Commission's ~800 local
landmarks) is not openly published: the City's open-data portal carries only
the local and federal historic *district* polygons, the Lakeshore Historic
District parcel browser covers one district and has no licence, and the
`Landmarks` MapServer the City's test web map points at no longer exists
(404 on 2026-11-23).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit  # noqa: E402
from shared.nrhp import national_register_table  # noqa: E402

if __name__ == "__main__":
    emit(national_register_table("USEVN", "Evanston", "evn"))
