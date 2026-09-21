#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for the Overture position-lookup staging models.

Emits the date of the current Overture release, read from the STAC catalog's
`latest` field.  Overture releases roughly monthly; a firing that finds the
same release as the staged parquet was built from exits `up_to_date`, and
the one that finds a newer release rebuilds.  An unreachable catalog
degrades to the epoch, so the parquet compares fresh and the firing no-ops
instead of failing (see `emit_freshness`).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "raw"))
from shared.ingest import emit_freshness  # noqa: E402
from shared.overture import latest_release, release_published_at  # noqa: E402


def released_at():
    return release_published_at(latest_release())


if __name__ == "__main__":
    emit_freshness(None, released_at, label="Overture release")
