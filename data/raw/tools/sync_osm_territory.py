"""Write each city's CITY_TERRITORY into its model's OSM datasource `where`.

Run after editing CITY_TERRITORY (from data/raw):

    uv run --no-project --with pyarrow --with "pytrilogy>=0.3.464" python tools/sync_osm_territory.py

A staged OSM extract keeps the territory it was cut with until its weekly
re-extract, so the city model filters it again on read (shared.ingest.
territory_where).  tests/test_city_territory.py fails while a model and the
table disagree.  Pass --check to only report the models that would change.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

RAW = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAW))

from shared.ingest import CITY_TERRITORY, territory_where  # noqa: E402

# The staged parquet's file line, then an optional `where` already written.
_OSM_FILE = re.compile(
    r"(file `https://storage\.googleapis\.com/trilogy_public_models/duckdb/staging/"
    r"(?P<lc>[a-z]+)_osm_staging\.parquet`)(?:\nwhere [^;]*)?;"
)


def sync(text: str) -> str:
    def write(m: re.Match) -> str:
        code = m.group("lc").upper()
        return f"{m.group(1)}\nwhere {territory_where(CITY_TERRITORY[code])};"

    return _OSM_FILE.sub(write, text)


def city_models() -> list[Path]:
    return sorted(p for p in RAW.glob("*/*_tree_info.preql") if _OSM_FILE.search(p.read_text(encoding="utf-8")))


def main(argv: list[str]) -> int:
    check = "--check" in argv
    changed = []
    for path in city_models():
        text = path.read_text(encoding="utf-8")
        synced = sync(text)
        if synced != text:
            changed.append(path.relative_to(RAW).as_posix())
            if not check:
                path.write_text(synced, encoding="utf-8", newline="\n")
    verb = "out of date" if check else "updated"
    print(f"{len(changed)} model(s) {verb}" + "".join(f"\n  {c}" for c in changed))
    return 1 if check and changed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
