#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["duckdb"]
# ///

"""Which cities' published tree parquets carry a column yet.

A city target's `where` is applied whenever its parquet is read, so a target
may gate on a new stored column only once every published parquet has it.
This reads each city's published schema (the parquet footer, over HTTPS) and
lists the cities still missing the column.

    uv run tools/published_columns.py reported_missing

Exits non-zero while any city is missing it.  Run from data/raw.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import duckdb

RAW = Path(__file__).resolve().parents[1]
TARGET_FILE = re.compile(r"file f`(https://[^`]*_tree_info_v\{data_version\}\.parquet)`")
DATA_VERSION = re.search(r"param data_version string default '([^']+)'", (RAW / "core.preql").read_text(encoding="utf-8")).group(1)


def published_urls() -> dict[str, str]:
    urls = {}
    for model in sorted(RAW.glob("*/*_tree_info.preql")):
        match = TARGET_FILE.search(model.read_text(encoding="utf-8"))
        if match:
            urls[model.parent.name.upper()] = match.group(1).replace("{data_version}", DATA_VERSION)
    return urls


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__)
        return 2
    column = argv[0]
    conn = duckdb.connect()
    conn.execute("INSTALL httpfs; LOAD httpfs;")
    missing = []
    for city, url in published_urls().items():
        try:
            names = {row[0] for row in conn.execute(f"DESCRIBE SELECT * FROM read_parquet('{url}')").fetchall()}
        except duckdb.Error as e:
            print(f"{city}: unreadable ({str(e).splitlines()[0][:80]})")
            missing.append(city)
            continue
        if column not in names:
            missing.append(city)
    total = len(published_urls())
    print(f"{total - len(missing)}/{total} published city parquets carry `{column}`")
    if missing:
        print("missing:", ", ".join(missing))
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
