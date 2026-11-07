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
import pyarrow.compute as pc

from shared.ingest import emit
from shared.wcvp import (
    DISTRIBUTION_COLUMNS,
    DISTRIBUTION_FLAGS,
    DISTRIBUTION_MEMBER,
    SOURCE_CITATION,
    constant_column,
    fetch_zip,
    read_member,
)


def build() -> pa.Table:
    table = read_member(fetch_zip(), DISTRIBUTION_MEMBER, DISTRIBUTION_COLUMNS)
    for flag in DISTRIBUTION_FLAGS:
        idx = table.schema.get_field_index(flag)
        table = table.set_column(idx, flag, pc.equal(table.column(flag), 1))
    return table.append_column(
        "source_version", constant_column(SOURCE_CITATION, table.num_rows)
    )


if __name__ == "__main__":
    emit(build())
