#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Every WCVP name, its status, and the accepted name it resolves to.

Adds two columns the checklist does not carry: `name_key`, the name folded
the way `shared.wcvp.name_key` folds a species value, so a lookup is an exact
equality, and `accepted_taxon_name`, the accepted record's name joined on
here so a consumer resolving a synonym needs no self-join.
"""

import pyarrow as pa

from shared.ingest import emit
from shared.wcvp import SOURCE_CITATION, constant_column, fetch_zip, names_table


def build() -> pa.Table:
    table = names_table(fetch_zip())
    return table.append_column(
        "source_version", constant_column(SOURCE_CITATION, table.num_rows)
    )

if __name__ == "__main__":
    emit(build())
