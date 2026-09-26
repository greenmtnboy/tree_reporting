"""Immutable Boston offset hypotheses; never changes curation or split membership."""
import csv
import hashlib
import json
from pathlib import Path
from urllib.parse import urlencode

import numpy as np
import pandas as pd

from urban_tree_ml.feedback import load_persisted_reviews
from urban_tree_ml.review_assignments import review_assignments
from urban_tree_ml.rescore_curation import labels_from_feedback
from urban_tree_ml.training_drift import offset_candidates


def build(root, output, count=25):
    model = "sf-boston-naip-curation-v3-retry2"
    frozen = root / "run-inputs/sf-boston-naip-curation-v4-finetune/usbos"
    evaluation = root / "runs" / model / "evaluation/validation-usbos"
    config = json.loads((evaluation / "evaluation-metadata.json").read_text())["config"]
    directory = root / "qa/registration/usbos-2023-external"
    manifest = json.loads((directory / "manifest.json").read_text())
    state = load_persisted_reviews(directory)
    done = {k for k, v in state["scene_reviews"].items() if v.get("done")}
    done_chips = {s.get("validation_chip_id") for s in manifest["scenes"] if s["scene_id"] in done}
    done_trees = {str(s["tree_id"]) for s in manifest["samples"] if s["scene_id"] in done}
    index = pd.read_parquet(root / "chips" / config["dataset"] / "chips.parquet")
    index = index[(index.split == "validation") & ~index.chip_id.isin(done_chips)
                  & index.candidate_tree_count.between(8, 60) & (index.collision_excluded_count <= 5)]
    feedback = json.loads((frozen / "training-feedback.json").read_text())
    truth = labels_from_feedback(pd.read_parquet(frozen / "inventory.parquet"), feedback,
        root / "imagery/usbos/2023/usbos-2023-external.vrt", 256,
        config["targets"]["output_stride"], set(index.chip_id))
    predictions = pd.read_parquet(evaluation / "predictions.parquet")
    scale = config["imagery"]["resolution_m"] * config["targets"]["output_stride"]
    candidates = []
    for chip, trees in truth.groupby("chip_id"):
        if set(trees.tree_id.astype(str)) <= done_trees:
            continue
        offsets = offset_candidates(predictions[predictions.chip_id == chip], trees, scale)
        if not offsets:
            continue
        median = float(np.median([r["distance_m"] for r in offsets]))
        candidates.append({"city": "usbos", "chip_id": chip, "queue": "diagnostic",
            "reason": f"Big-tree offsets: {len(offsets)} mutual-nearest predictions 3–12 m from DBH ≥16 in targets; median {median:.1f} m. Hypotheses, not confirmed tree matches.",
            "offset_count": len(offsets), "median_offset_m": median})
    candidates.sort(key=lambda r: (-r["offset_count"], -r["median_offset_m"], r["chip_id"]))
    if len(candidates) < count:
        raise ValueError(f"Only {len(candidates)} eligible; no silent relaxation")
    rows = candidates[:count]
    # Keep the other city's assignments intact: discovery selects one snapshot per run.
    previous = review_assignments(root, model, "ussfo")
    rows += [{"city": "ussfo", "chip_id": chip, **item} for chip, item in previous["chips"].items()]
    output.mkdir(parents=True, exist_ok=False)
    target = output / "validation-targets.csv"
    with target.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=["city", "chip_id", "queue", "reason", "offset_count", "median_offset_m"])
        writer.writeheader(); writer.writerows(rows)
    metadata = {"new_run": model, "test_evaluated": False, "boston_selected": count,
        "candidate_pool": len(candidates), "review_revision": state["state_revision"],
        "preserved_sf_snapshot": previous["snapshot"],
        "inputs": {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in
                   [frozen / "training-feedback.json", evaluation / "predictions.parquet"]},
        "output_sha256": {target.name: hashlib.sha256(target.read_bytes()).hexdigest()}}
    (output / "manifest.json").write_text(json.dumps(metadata, indent=2))
    links = ["# Boston big-tree validation follow-up", "", "25 unreviewed, moderate-inventory-density validation chips. Saved parent predictions against the new run's frozen labels. No test data or annotations changed.", ""]
    for row in rows[:count]:
        query = urlencode({"run": model + "::validation-usbos", "city": "usbos",
                           "assignment": "diagnostic", "unreviewed": 1, "chip": row["chip_id"], "radius": "4.0"})
        links.append(f"- [{row['chip_id']}](http://127.0.0.1:8765/model?{query}): {row['reason']}")
    (output / "REPORT.md").write_text("\n".join(links), encoding="utf-8")
    (output / "COMPLETE").write_text("Queue complete; annotations untouched.\n")
    return metadata


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.root, args.output), indent=2))
