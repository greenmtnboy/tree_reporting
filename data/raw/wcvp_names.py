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
import pyarrow.compute as pc

from shared.ingest import emit
from shared.wcvp import NAME_COLUMNS, NAMES_MEMBER, SOURCE_CITATION, constant_column, fetch_zip, name_key_array, read_member


def build() -> pa.Table:
    table = read_member(fetch_zip(), NAMES_MEMBER, NAME_COLUMNS)
    ids = table.column("plant_name_id")
    names = table.column("taxon_name")
    index = pc.index_in(table.column("accepted_plant_name_id"), value_set=ids)
    accepted = pc.take(names, index)
    table = table.append_column("name_key", name_key_array(names))
    table = table.append_column("accepted_taxon_name", accepted)
    return table.append_column(
        "source_version", constant_column(SOURCE_CITATION, table.num_rows)
    )


if __name__ == "__main__":
    emit(build())
