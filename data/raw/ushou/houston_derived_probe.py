#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""USHOU's derived-tables watermark. Written by tools/sync_city_derived.py; see shared/city_derived.py."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.city_derived import main  # noqa: E402

if __name__ == "__main__":
    main("USHOU")
