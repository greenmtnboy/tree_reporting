#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""WCVP distributions: one row per accepted taxon and TDWG level-3 area.

The 0/1 flags (`introduced`, `extinct`, `location_doubtful`) are published as
booleans.  A row without `introduced` is native there.
"""

import pyarrow as pa

from shared.ingest import emit
from shared.wcvp import SOURCE_CITATION, constant_column, distribution_table, fetch_zip


def build() -> pa.Table:
    table = distribution_table(fetch_zip())
    return table.append_column(
        "source_version", constant_column(SOURCE_CITATION, table.num_rows)
    )

if __name__ == "__main__":
    emit(build())
