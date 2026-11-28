#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Bozeman, Montana's National Register listings.

**The National Register of Historic Places** (the National Park Service's
`cultural_resources/nrhp_locations` MapServer, points then polygons), read
live, within `CITY_BOUNDS["USBZN"]` and with `City = Bozeman`.  Listed
buildings, sites and structures, keyed `bozeman-nr-{NRIS_Refnum}` with
`CertDate` as `year_designated`; Bozeman's eleven historic districts are
areas and are not published.  NPS data is a US federal work.

The City's own `Public/Historic_Structures/FeatureServer` on gisweb.bozeman.net
is not read: its "Individually Listed Properties" layer (48 points) is the
same set of National Register listings under the City's spelling of the
names (`Lewis, Samuel Hosue`, `RT & Barnett Co. Building`), with no register
number and no licence stated, and its "Historic Structures" layer (2,412
points) is the historic-resource survey -- contributing, eligible and
non-eligible buildings -- not a designation.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit  # noqa: E402
from shared.nrhp import national_register_table  # noqa: E402

if __name__ == "__main__":
    emit(national_register_table("USBZN", "Bozeman", "bozeman"))
