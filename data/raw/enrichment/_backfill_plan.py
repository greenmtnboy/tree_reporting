"""What backfill.py decides without calling anything: which species to ask, in
what order, what has been asked already, and whether a checkpoint is safe to
publish.  Plain rows in, plain rows out, so the tests need neither the LLM
client nor the network.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

# Rewritten on every write from the other columns; never evidence of a change.
DERIVED_COLUMNS = frozenset({"is_complete"})

# An answer was not obtained, as opposed to an answer that was empty.  A
# species logged with one of these may be asked again (`--retry-failed`); one
# that answered, with values or without, is never re-asked.
RETRYABLE_STATUSES = frozenset({"llm_failed"})


def is_empty(value) -> bool:
    return value is None or value == [] or value == ""


def tree_ordered(species: list[str], tree_counts: dict[str, int]) -> list[str]:
    """Most trees first, the name breaking ties so two runs agree."""
    return sorted(species, key=lambda s: (-tree_counts.get(s, 0), s))


def covering(ordered: list[str], tree_counts: dict[str, int], share: float) -> list[str]:
    """The shortest prefix of *ordered* that holds *share* of its trees.

    The cut is taken over the whole list, done and not done alike, so it lands
    on the same species in every batch of a resumed run.
    """
    total = sum(tree_counts.get(s, 0) for s in ordered)
    if total <= 0 or share >= 1:
        return list(ordered)
    kept: list[str] = []
    running = 0
    for name in ordered:
        if running >= share * total:
            break
        kept.append(name)
        running += tree_counts.get(name, 0)
    return kept


def read_asked(path: str | None) -> dict[str, str]:
    """species -> the status of its last attempt, from the asked log."""
    if not path or not Path(path).exists():
        return {}
    asked: dict[str, str] = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            entry = json.loads(line)
            asked[entry["species"]] = entry["status"]
    return asked


def record_asked(path: str | None, species: str, status: str, changed: list[str]) -> None:
    """One line per species asked, appended before the next one starts.

    The checkpoint cannot say this: a field the model left null is null before
    and after, so without the log a resumed run would ask the same species
    again, for ever, and call the pressure coverage.
    """
    if not path:
        return
    entry = {
        "species": species,
        "status": status,
        "changed": changed,
        "at": datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
    }
    with Path(path).open("a", encoding="utf-8") as log:
        log.write(json.dumps(entry, ensure_ascii=False) + "\n")


def already_asked(asked: dict[str, str], retry_failed: bool) -> set[str]:
    return {s for s, status in asked.items() if not (retry_failed and status in RETRYABLE_STATUSES)}


def rebase_on_published(checkpoint: list[dict], published: list[dict], fields: list[str]) -> tuple[list[dict], dict[str, int]]:
    """The published rows, with the checkpoint's fills laid on their nulls.

    A checkpoint is hours old by the time it is uploaded, and the daily job or
    the admin form may have published in between.  So the published table is
    the base and wins everywhere: its rows, its order, every value it holds.
    The checkpoint contributes one thing, a value for a requested field the
    published row still has empty.  A row only the checkpoint has was removed
    upstream on purpose and stays removed.

    Returns the rows and how many values each field gained.
    """
    mine = {row["species"]: row for row in checkpoint}
    gained = {f: 0 for f in fields}
    rebased: list[dict] = []
    for live in published:
        row = dict(live)
        filled = mine.get(row["species"])
        if filled:
            for f in fields:
                if is_empty(row.get(f)) and not is_empty(filled.get(f)):
                    row[f] = filled[f]
                    gained[f] += 1
        rebased.append(row)
    return rebased, gained


def regressions(candidate: list[dict], published: list[dict], limit: int = 20) -> list[str]:
    """Everything the published table holds that *candidate* would lose.

    A missing row, or a value that was there and is now gone or different, in
    any column but the derived ones.  Empty means the upload only adds.
    """
    mine = {row["species"]: row for row in candidate}
    problems: list[str] = []
    for live in published:
        species = live["species"]
        row = mine.get(species)
        if row is None:
            problems.append(f"{species}: row missing")
            continue
        for column, value in live.items():
            if column in DERIVED_COLUMNS or is_empty(value):
                continue
            if row.get(column) != value:
                problems.append(f"{species}.{column}: {value!r} -> {row.get(column)!r}")
    if len(problems) > limit:
        return problems[:limit] + [f"... and {len(problems) - limit} more"]
    return problems
