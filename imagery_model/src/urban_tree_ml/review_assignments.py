"""Read-only discovery of completed, run-scoped benchmark review queues."""

import csv
import hashlib
import json


def review_assignments(root, run_id, city):
    for directory in sorted((root / "benchmarks").glob("*"), reverse=True):
        if not (directory / "COMPLETE").exists():
            continue
        try:
            manifest = json.loads((directory / "manifest.json").read_text())
            if manifest.get("new_run") != run_id:
                continue
            path = directory / "validation-targets.csv"
            expected = manifest.get("output_sha256", {}).get(path.name)
            if expected != hashlib.sha256(path.read_bytes()).hexdigest():
                continue
            with path.open(newline="", encoding="utf-8") as handle:
                rows = [row for row in csv.DictReader(handle) if row["city"] == city.lower()]
            return {
                "snapshot": directory.name,
                "chips": {
                    row["chip_id"]: {"queue": row["queue"], "reason": row["reason"]} for row in rows
                },
            }
        except (OSError, ValueError, KeyError):
            continue
    return {"snapshot": None, "chips": {}}
