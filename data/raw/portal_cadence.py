#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "tzdata"]  # tzdata: pyarrow needs it to read tz-aware IPC on Windows
# ///
"""Measure how often each city's upstream actually publishes, and compare that
to how often we poll it.

Every city's ingest is scheduled independently (one `[[cloud.job]]` per city in
`../trilogy.toml`), which only pays off if the cron matches the portal's real
publishing rhythm.  Nothing upstream advertises that rhythm reliably --
`accrualPeriodicity` is aspirational where it exists at all -- so the only
honest source is the watermark the city's own freshness probe already emits,
sampled over time.

Two modes, and the second is the one that matters:

    uv run ./portal_cadence.py                 # snapshot: watermark + age + cron
    uv run ./portal_cadence.py --record        # snapshot, appended to history

`--record` writes into `portal_cadence.json` (committed).  A single sample
cannot measure a cadence -- "Vancouver published 3 days ago" is consistent with
daily and with annual -- so cadence is derived from *changes* between samples:
each distinct watermark this file has ever seen is one publication, and the
median gap between them is the observed interval.  Run it periodically; the
file is the record.

The verdict column compares that observed interval against the shortest gap
between the city's own cron firings:

    over-polled     we poll much more often than the portal publishes
    matched         the two are within a factor of ~2
    under-polled    the portal publishes faster than we look
    unknown         fewer than two distinct watermarks recorded yet

An over-polled city is not broken -- a probe that finds nothing stale exits
`up_to_date` in seconds -- it is just paying a VM boot for an answer we could
have predicted.  Under-polled is the one to act on: it means the map is
carrying data older than it needs to be.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import subprocess
import sys
import tomllib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median

import pyarrow as pa
import pyarrow.ipc as ipc

RAW_DIR = Path(__file__).resolve().parent
DATA_DIR = RAW_DIR.parent
CONFIG = DATA_DIR / "trilogy.toml"
HISTORY = RAW_DIR / "portal_cadence.json"

# A probe that reports the epoch is `emit_freshness` degrading a dead portal,
# not a publication.  Recording it would invent a "publication" on every
# outage and halve every measured interval.
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)

# kind -> (glob under a city directory, the [[cloud.job]] key prefix that polls
# it).  The municipal portals are the point of the exercise; the other two are
# here because the same question applies to them and the answer is cheap once
# the runner exists.
KINDS = {
    "portal": ("*_update_time.py", "city-"),
    "osm": ("*_osm_probe.py", "osm-"),
    "landmarks": ("*_landmarks_probe.py", "landmarks-"),
}


def city_dirs() -> list[Path]:
    return sorted(
        p for p in RAW_DIR.iterdir()
        if p.is_dir() and re.fullmatch(r"[a-z]{5}", p.name)
    )


def probes(kinds: list[str]) -> list[tuple[str, str, Path]]:
    """(city code, kind, script) for every probe of the requested kinds."""
    found = []
    for city in city_dirs():
        for kind in kinds:
            pattern, _prefix = KINDS[kind]
            for script in sorted(city.glob(pattern)):
                found.append((city.name.upper(), kind, script))
    return found


def run_probe(script: Path) -> tuple[datetime | None, str]:
    """Run one probe the way Trilogy does and read its Arrow IPC watermark."""
    try:
        result = subprocess.run(
            ["uv", "run", str(script)],
            capture_output=True,
            timeout=180,
            cwd=script.parent,
        )
    except subprocess.TimeoutExpired:
        return None, "timed out"
    if result.returncode != 0:
        tail = result.stderr.decode("utf-8", "replace").strip().splitlines()
        return None, f"exit {result.returncode}: {tail[-1] if tail else 'no output'}"
    try:
        table = ipc.open_stream(io.BytesIO(result.stdout)).read_all()
        value = table.column("data_updated_through")[0].as_py()
    except (pa.ArrowInvalid, KeyError, IndexError) as err:
        return None, f"unreadable output: {err}"
    if value is None:
        return None, "null watermark"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    if value <= EPOCH:
        # emit_freshness's PORTAL_UNAVAILABLE_TIMESTAMP.
        return None, "portal unavailable (probe degraded to the epoch)"
    return value.astimezone(timezone.utc), ""


# ---------------------------------------------------------------------------
# Cron
# ---------------------------------------------------------------------------

def _field(spec: str, low: int, high: int) -> set[int]:
    values: set[int] = set()
    for part in spec.split(","):
        step = 1
        if "/" in part:
            part, _, step_s = part.partition("/")
            step = int(step_s)
        if part in ("*", "?"):
            start, end = low, high
        elif "-" in part:
            start_s, _, end_s = part.partition("-")
            start, end = int(start_s), int(end_s)
        else:
            start = end = int(part)
        values.update(v for v in range(start, end + 1) if (v - start) % step == 0)
    return values


def shortest_gap(cron: str) -> timedelta | None:
    """Shortest interval between two firings of a 6-field (second-first) cron.

    Enumerated over four weeks rather than reasoned about: the expressions here
    are simple, but "shortest gap" for a list of hours on selected weekdays is
    not something to derive in your head, and being wrong here silently
    mislabels a city.
    """
    parts = cron.split()
    if len(parts) != 6:
        return None
    try:
        _sec, minute, hour, dom, month, dow = (
            _field(part, low, high)
            for part, low, high in zip(
                parts, (0, 0, 0, 1, 1, 0), (59, 59, 23, 31, 12, 6)
            )
        )
    except ValueError:
        return None
    start = datetime(2026, 1, 5, tzinfo=timezone.utc)  # a Monday
    fires = []
    for i in range(4 * 7 * 24 * 60):
        moment = start + timedelta(minutes=i)
        if (
            moment.minute in minute
            and moment.hour in hour
            and moment.day in dom
            and moment.month in month
            and (moment.weekday() + 1) % 7 in dow
        ):
            fires.append(moment)
    if len(fires) < 2:
        return None
    return min(b - a for a, b in zip(fires, fires[1:]))


def job_crons() -> dict[tuple[str, str], str]:
    """(city code, kind) -> cron, read from trilogy.toml's [[cloud.job]] array.

    Read rather than duplicated: a table of crons in this script would be one
    more thing to keep in sync with the file that actually schedules them.
    """
    parsed = tomllib.loads(CONFIG.read_text(encoding="utf-8"))
    crons: dict[tuple[str, str], str] = {}
    for job in parsed.get("cloud", {}).get("job", []):
        key, cron = job.get("key", ""), job.get("schedule")
        if not cron:
            continue
        for kind, (_glob, prefix) in KINDS.items():
            if key.startswith(prefix):
                crons[(key[len(prefix):].upper(), kind)] = cron
    return crons


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------

def load_history() -> dict[str, list[str]]:
    if not HISTORY.exists():
        return {}
    return json.loads(HISTORY.read_text(encoding="utf-8")).get("watermarks", {})


def save_history(watermarks: dict[str, list[str]]) -> None:
    payload = {
        "_comment": (
            "Distinct freshness watermarks observed per probe, oldest first, "
            "written by portal_cadence.py --record. Each entry is one upstream "
            "publication; the gaps between them are the measured cadence the "
            "per-city crons in ../trilogy.toml are set from. Append-only in "
            "practice -- deleting entries throws away the only cadence "
            "measurement this project has."
        ),
        "watermarks": {key: value for key, value in sorted(watermarks.items())},
    }
    HISTORY.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def observed_interval(samples: list[str]) -> timedelta | None:
    if len(samples) < 2:
        return None
    stamps = sorted(datetime.fromisoformat(s) for s in samples)
    return median([b - a for a, b in zip(stamps, stamps[1:])])


def verdict(observed: timedelta | None, poll: timedelta | None) -> str:
    if observed is None or poll is None:
        return "unknown"
    if observed > poll * 2:
        return "over-polled"
    if poll > observed * 2:
        return "under-polled"
    return "matched"


def human(delta: timedelta | None) -> str:
    if delta is None:
        return "-"
    days, seconds = delta.days, delta.seconds
    if days >= 365:
        return f"{days / 365.25:.1f}y"
    if days >= 1:
        return f"{days}d"
    if seconds >= 3600:
        return f"{seconds // 3600}h"
    return f"{seconds // 60}m"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--kind",
        choices=[*KINDS, "all"],
        default="portal",
        help="which probes to run (default: the municipal portals)",
    )
    parser.add_argument("--city", action="append", help="limit to these city codes")
    parser.add_argument(
        "--record",
        action="store_true",
        help=f"append newly seen watermarks to {HISTORY.name}",
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--concurrency", type=int, default=6)
    args = parser.parse_args()

    kinds = list(KINDS) if args.kind == "all" else [args.kind]
    wanted = {code.upper() for code in args.city} if args.city else None
    targets = [p for p in probes(kinds) if wanted is None or p[0] in wanted]
    if not targets:
        print("no probes matched", file=sys.stderr)
        return 1

    crons = job_crons()
    history = load_history()
    now = datetime.now(timezone.utc)

    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        results = list(pool.map(lambda target: run_probe(target[2]), targets))

    rows = []
    for (code, kind, script), (watermark, note) in zip(targets, results):
        # Keyed by script rather than by city: Boston polls four inventories,
        # and averaging them would describe none of them.
        key = f"{kind}:{script.parent.name}/{script.name}"
        seen = history.get(key, [])
        if args.record and watermark is not None:
            stamp = watermark.isoformat()
            if stamp not in seen:
                seen = sorted([*seen, stamp])
                history[key] = seen
        poll = shortest_gap(crons.get((code, kind), ""))
        interval = observed_interval(seen)
        rows.append(
            {
                "city": code,
                "kind": kind,
                "probe": script.name,
                "watermark": watermark.isoformat() if watermark else None,
                "age": human(now - watermark) if watermark else None,
                "publications_seen": len(seen),
                "observed_interval": human(interval),
                "cron": crons.get((code, kind)),
                "poll_interval": human(poll),
                "verdict": verdict(interval, poll),
                "note": note,
            }
        )

    if args.record:
        save_history(history)

    if args.json:
        print(json.dumps(rows, indent=2))
        return 0

    width = max(len(row["probe"]) for row in rows)
    print(
        f"{'city':<6} {'probe':<{width}} {'watermark':<20} {'age':>6} "
        f"{'seen':>4} {'observed':>8} {'polled':>7}  verdict"
    )
    for row in sorted(rows, key=lambda r: (r["kind"], r["city"])):
        stamp = (row["watermark"] or "").replace("+00:00", "")[:19] or "-"
        print(
            f"{row['city']:<6} {row['probe']:<{width}} {stamp:<20} "
            f"{row['age'] or '-':>6} {row['publications_seen']:>4} "
            f"{row['observed_interval']:>8} {row['poll_interval']:>7}  "
            f"{row['verdict']}" + (f"  ({row['note']})" if row["note"] else "")
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
