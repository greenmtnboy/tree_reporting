#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///

"""Emit approved tree reports, resolved to one correction per tree.

Source of truth is the public export the reviewer writes when it approves a
tree report (`reviewer/modifications.ts`): `community/tree_modifications.ndjson`,
every approved modification in publish order.  Like community_tree_info.py it
reads the public GCS object, never Firestore, so the ingest needs no
credentials; a missing export (nothing approved yet) is zero rows.

A tree can collect several reports, and the latest one wins:

* each field (species, DBH, position) takes the last non-null value any
  `update` gave it -- a later report that leaves a field alone keeps the
  earlier correction;
* a report can instead clear the DBH (`clearFields` names `dbhInches`): the recorded
  value is wrong and nobody has a better one.  That clears any earlier
  corrected DBH and publishes the tree with none, until a later report gives
  a value;
* the tree is reported missing exactly when its latest report is `missing`,
  so a later `update` (the tree is there after all) restores it;
* a report can name the tree and say why it is notable (`treeName`,
  `significance`, `significanceSource`; docs/NAMED_TREES_AND_CROWN_POSITION.md).
  Each is free text the reviewer approved, trimmed and length-capped here.
  `clearFields` naming one of them publishes it as the empty string, which
  the model reads as "none, whatever the source says" (`nullif` in
  ../tree_dedup.preql), the way `dbh_unknown` clears a diameter.

A proposed species goes through the same `sanitize_species` folding as every
other source, so it publishes as the accepted scientific name; a value that
names no taxon (and no growth form) is dropped rather than overwriting the
tree with Unknown -- null here means "keep what the source says".  A position
is taken only as a pair.

The rows carry the tree id the reporter saw on the map, which is the id the
published row has; ../tree_dedup.preql merges this key partially into
`tree_id`, so a report only ever touches the tree it names.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping

import pyarrow as pa
import requests

sys.path.insert(0, str(Path(__file__).parent))
from shared.ingest import emit, form_sentinel_for, sanitize_species  # noqa: E402

PUBLISHED_BUCKET = os.environ.get(
    "COMMUNITY_PUBLISHED_BUCKET", "sf-tree-reporting-published"
)
EXPORT_URL = os.environ.get(
    "TREE_MODIFICATIONS_EXPORT_URL",
    f"https://storage.googleapis.com/{PUBLISHED_BUCKET}/community/tree_modifications.ndjson",
)
# A local NDJSON file instead of the bucket, for testing a build against a fixture.
EXPORT_PATH = os.environ.get("TREE_MODIFICATIONS_EXPORT_PATH")

# A DBH beyond this is a typo (the reviewer's form takes inches).
MAX_DBH_INCHES = 200.0

# The longest name, blurb and attribution a report can publish; the public
# form and the reviewer hold the same limits.  Longer text is cut, not dropped:
# the reviewer approved it.
MAX_TREE_NAME_CHARS = 60
MAX_SIGNIFICANCE_CHARS = 500
MAX_SIGNIFICANCE_SOURCE_CHARS = 300

# Export field -> (published column, length cap).
TEXT_FIELDS = {
    "treeName": ("tree_name", MAX_TREE_NAME_CHARS),
    "significance": ("significance", MAX_SIGNIFICANCE_CHARS),
    "significanceSource": ("significance_source", MAX_SIGNIFICANCE_SOURCE_CHARS),
}

SCHEMA = pa.schema(
    [
        ("tree_id", pa.string()),
        ("species", pa.string()),
        ("diameter_at_breast_height", pa.float64()),
        ("dbh_unknown", pa.bool_()),
        ("latitude", pa.float64()),
        ("longitude", pa.float64()),
        ("missing", pa.bool_()),
        ("tree_name", pa.string()),
        ("significance", pa.string()),
        ("significance_source", pa.string()),
    ]
)


def load_records() -> list[Mapping[str, Any]]:
    """The reviewer's approved-modification export, in publish order."""
    if EXPORT_PATH:
        text = Path(EXPORT_PATH).read_text(encoding="utf-8")
    else:
        response = requests.get(EXPORT_URL, timeout=60)
        if response.status_code == 404:
            print(f"Tree modifications: no export at {EXPORT_URL}; emitting 0 rows", file=sys.stderr)
            return []
        response.raise_for_status()
        text = response.text
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and number not in (float("inf"), float("-inf")) else None


def clean_species(value: Any) -> str | None:
    """The accepted name a report proposes, a growth-form sentinel, or None (keep the source's)."""
    if not isinstance(value, str) or not value.strip():
        return None
    return sanitize_species(value) or form_sentinel_for(value)


def clean_text(value: Any, limit: int) -> str | None:
    """Free text a report gave, whitespace collapsed and capped; None when there is none."""
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    return text[:limit].rstrip() or None


def resolve(records: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """One row per reported tree: the latest value of each field, and whether the latest report says missing."""
    # Publish order: the export is written that way, and publishedAt breaks no
    # tie it does not have to (a stable sort keeps file order for equal stamps).
    ordered = sorted(
        enumerate(records),
        key=lambda pair: (str(pair[1].get("publishedAt") or ""), pair[0]),
    )
    trees: dict[str, dict[str, Any]] = {}
    for _, record in ordered:
        tree_id = str(record.get("treeId") or "").strip()
        if not tree_id:
            continue
        tree = trees.setdefault(
            tree_id,
            {"tree_id": tree_id, "species": None, "diameter_at_breast_height": None,
             "dbh_unknown": False, "latitude": None, "longitude": None, "missing": False,
             "tree_name": None, "significance": None, "significance_source": None},
        )
        if record.get("kind") == "missing":
            tree["missing"] = True
            continue
        tree["missing"] = False
        species = clean_species(record.get("species"))
        if species is not None:
            tree["species"] = species
        dbh = _float(record.get("dbhInches"))
        cleared = record.get("clearFields")
        if isinstance(cleared, list) and "dbhInches" in cleared:
            tree["diameter_at_breast_height"], tree["dbh_unknown"] = None, True
        elif dbh is not None and 0 < dbh <= MAX_DBH_INCHES:
            tree["diameter_at_breast_height"], tree["dbh_unknown"] = dbh, False
        lat, lon = _float(record.get("latitude")), _float(record.get("longitude"))
        if lat is not None and lon is not None and abs(lat) <= 90 and abs(lon) <= 180:
            tree["latitude"], tree["longitude"] = lat, lon
        for field, (column, limit) in TEXT_FIELDS.items():
            if isinstance(cleared, list) and field in cleared:
                tree[column] = ""
                continue
            text = clean_text(record.get(field), limit)
            if text is not None:
                tree[column] = text
    return [trees[k] for k in sorted(trees)]


def records_to_table(records: Iterable[Mapping[str, Any]]) -> pa.Table:
    return pa.Table.from_pylist(resolve(records), schema=SCHEMA)


def main() -> None:
    emit(records_to_table(load_records()))


if __name__ == "__main__":
    main()
