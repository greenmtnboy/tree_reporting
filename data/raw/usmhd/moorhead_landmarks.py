#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Moorhead, Minnesota's National Register of Historic Places listings.

The City publishes no local landmark register of its own (its ArcGIS
organisation `CityOfMoorheadMN` holds Sanborn map scans and assessor layers,
no designation layer; checked 2026-11-23), so the one register is the
**National Register of Historic Places**: the National Park Service's
`cultural_resources/nrhp_locations` MapServer, points then polygons, within
`CITY_BOUNDS["USMHD"]` and with `City = Moorhead`.  The box crosses the Red
River into downtown Fargo, North Dakota, whose listings (the Fargo Theatre,
the Cass County Courthouse ...) are filed under `City = Fargo` and are not
Moorhead's.  Listed buildings, sites, structures and objects, keyed
`mhd-nr-{NRIS_Refnum}` with `CertDate`'s year as `year_designated`;
districts are areas and are not published, and a `STATUS` other than
`Listed` (a removed listing) is skipped.  NPS data is a US federal work.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit  # noqa: E402
from shared.nrhp import national_register_table  # noqa: E402

if __name__ == "__main__":
    emit(national_register_table("USMHD", "Moorhead", "mhd"))
