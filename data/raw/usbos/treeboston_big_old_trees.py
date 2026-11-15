#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy", "openpyxl"]
# ///
"""Tree Boston's Big Old Trees of Boston: a register of the city's notable trees.

Source: the registry behind https://treeboston.org/bot/ -- a public Google
Sheet Tree Boston's volunteers fill in, one tab per neighbourhood, recording
"large and significant trees in the City of Boston [that] merit special
recognition because of either their age, size, form, or historical-cultural
associations".  Read whole as one XLSX export, so a new neighbourhood tab is
picked up without a change here.  No licence is stated; the page invites the
public to use the map and the sheet, and this publishes only what the sheet
shows, credited.

A `NOTABLE_USBOS` partition (tree_dedup.preql, "notable row"):

  * **Only trees with a point of their own.**  Of the ~70 rows (2026-11-15),
    most carry an address or a park name and no coordinates, and the six Cedar
    Grove Cemetery trees share the cemetery's one point (4 decimals, ~10 m).
    A shared point is a site, not a tree, so rows whose coordinates repeat are
    dropped with the unplaced ones, and both are counted in the log.  They
    publish when a volunteer pins them.
  * **Public trees only.**  A row marked `Private` is a tree on someone's
    property; the map is of public trees.
  * **Linked** by shared/notable.py to the City of Boston inventory row of the
    same genus and a matching trunk within 30 m, read through
    boston_tree_info.py's own reader so the id exists in the build; an
    unlinked tree publishes at the registry's point.
  * **No name**: the registry gives none.  The significance says the tree is
    on the registry and gives the trunk the volunteers measured; heights are
    not quoted (an elm recorded at 214 ft).
  * **A volunteer sheet must not take Boston down.**  This partition sits in
    the same build as 1.5M other rows.  An unreachable sheet or a layout this
    does not recognise publishes *no* registry rows, loudly in the log, and
    the build goes on; there is no `@source_kept` guard on the label, and the
    trees lose their glow until the sheet reads again.
  * No freshness probe (the export carries no modified time): edits publish
    with the next change to any other Boston source, as Belmont's do.
"""

from __future__ import annotations

import io
import re
import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import (  # noqa: E402
    UpstreamUnavailable,
    emit,
    enforce_tree_schema,
    get_with_retry,
    validate_coordinates,
)
from shared.notable import link_to_inventory, notable_table  # noqa: E402

SHEET_ID = "1v6Ua9nelCbvc2R4HznT_I8_s-2eMO7mr69gU3UIQOHs"
EXPORT_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=xlsx"
REGISTRY_URL = "https://treeboston.org/bot/"
DATA_SOURCE = "NOTABLE_USBOS"

# Header cells, matched on their leading word: the tabs spell them
# differently ("DBH" / "DBH (in.)", and "Latitutde" as typed).
HEADERS = {
    "tree": "tree number",
    "neighborhood": "neighborhood",
    "latitude": "lati",
    "longitude": "longi",
    "species": "scientific name",
    "dbh": "dbh",
    "access": "private or public",
}


class LayoutChanged(RuntimeError):
    """The sheet no longer looks like the registry this reads."""


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def slugify(value: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", value.lower())).strip("-")


def number(value) -> float | None:
    """`41.5 in.`, `41.5` or 41.5 -> 41.5."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"\d+(?:\.\d+)?", str(value))
    return float(match.group()) if match else None


def column_index(header: tuple) -> dict[str, int]:
    cells = [(clean(c) or "").lower() for c in header]
    index = {}
    for key, prefix in HEADERS.items():
        hits = [i for i, c in enumerate(cells) if c.startswith(prefix)]
        if not hits:
            raise LayoutChanged(f"no {prefix!r} column in {cells}")
        index[key] = hits[0]
    return index


def read_sheets(body: bytes) -> list[tuple[str, list[tuple]]]:
    import openpyxl

    book = openpyxl.load_workbook(io.BytesIO(body), read_only=True, data_only=True)
    return [(ws.title, list(ws.iter_rows(values_only=True))) for ws in book.worksheets]


def registry_rows(sheets: list[tuple[str, list[tuple]]]) -> list[dict]:
    rows: list[dict] = []
    unplaced = private = 0
    for title, cells in sheets:
        if not cells:
            continue
        index = column_index(cells[0])
        for raw in cells[1:]:
            get = lambda key: raw[index[key]] if index[key] < len(raw) else None  # noqa: E731
            label = clean(get("tree"))
            ordinal = re.search(r"\d+", label or "")
            if not label or not label.lower().startswith("tree") or not ordinal:
                continue
            species = clean(get("species"))
            if species is None:
                continue  # a numbered row nobody has filled in
            if (clean(get("access")) or "").lower().startswith("private"):
                private += 1
                continue
            lat, lon = number(get("latitude")), number(get("longitude"))
            if lat is None or lon is None:
                unplaced += 1
                continue
            if lon > 0:
                lon = -lon
            neighbourhood = clean(get("neighborhood")) or clean(title)
            dbh = number(get("dbh"))
            text = (
                f"On Tree Boston's Big Old Trees of Boston, a volunteer registry "
                f"of the city's large and significant trees ({neighbourhood})."
            )
            if dbh:
                text += f" Trunk {dbh:g} in across, as the volunteers measured it."
            rows.append(
                {
                    "tree_id": f"treeboston-{slugify(title)}-{int(ordinal.group())}",
                    "species": species,
                    "tree_name": None,
                    "latitude": lat,
                    "longitude": lon,
                    "dbh_in": dbh,
                    "significance": text,
                    "significance_source": REGISTRY_URL,
                }
            )
    # Two trees at one point are a site's coordinates, not a tree's.
    seen: dict[tuple[float, float], int] = {}
    for r in rows:
        key = (round(r["latitude"], 6), round(r["longitude"], 6))
        seen[key] = seen.get(key, 0) + 1
    shared = [r for r in rows if seen[(round(r["latitude"], 6), round(r["longitude"], 6))] > 1]
    rows = [r for r in rows if r not in shared]
    print(
        f"Tree Boston registry: {len(rows)} placed public trees kept; dropped "
        f"{unplaced} with no coordinates, {len(shared)} sharing one point, "
        f"{private} private",
        file=sys.stderr,
    )
    return rows


def build(rows: list[dict], inventory: pa.Table | None) -> pa.Table:
    links = link_to_inventory(rows, inventory, label="Tree Boston")
    table = notable_table(rows, links, city="USBOS")
    if rows:  # it refuses an empty table, which is this source's degraded case
        table = validate_coordinates(table, city="Boston", city_code="USBOS")
    return enforce_tree_schema(
        table, city="Boston", data_source=DATA_SOURCE, proper_names=True
    )


def fetch_rows() -> list[dict]:
    """The registry, or no rows when the sheet cannot be read (see above)."""
    try:
        body = get_with_retry(EXPORT_URL, timeout=120).content
        return registry_rows(read_sheets(body))
    except (UpstreamUnavailable, LayoutChanged, OSError, ValueError, KeyError) as exc:
        detail = " ".join(str(exc).split())[:300]
        print(
            f"Tree Boston registry: NOT READ ({type(exc).__name__}: {detail}); "
            f"publishing no registry rows this build",
            file=sys.stderr,
        )
        return []


def boston_inventory() -> pa.Table:
    """The City of Boston rows exactly as boston_tree_info.py publishes them,
    under the canonical column names shared/notable.py reads."""
    sys.path.insert(0, str(Path(__file__).parent))
    import boston_tree_info  # noqa: E402

    table = boston_tree_info.load_published()
    return pa.table(
        {
            "tree_id": table["id"],
            "species": table["spp_bot"],
            "latitude": table["latitude"],
            "longitude": table["longitude"],
            "diameter_at_breast_height": table["dbh"],
        }
    )


if __name__ == "__main__":
    rows = fetch_rows()
    emit(build(rows, boston_inventory() if rows else None))
