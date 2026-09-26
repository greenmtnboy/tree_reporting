"""Rank saved training inference by missed targets without changing annotations."""

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from urban_tree_ml.feedback import load_persisted_reviews
from urban_tree_ml.frozen_comparison import counts, match_frame
from urban_tree_ml.training_drift import MODEL, REVIEWS


def build(root, output, count=20, model=MODEL, inference_run="sf-boston-train-offset-audit-v1", tag="most missed - 20260910", exclude_assigned=False, rank_by="missed"):
    if rank_by not in {"missed", "trees"}:
        raise ValueError("rank_by must be missed or trees")
    prior = {}
    assignment_paths = sorted((root / "review-queues").glob("sf-boston-train-offset-*.json")) if exclude_assigned else []
    for path in assignment_paths:
        for city, data in json.loads(path.read_text())["cities"].items():
            prior.setdefault(city, set()).update(item["chip_id"] for item in data["items"])
    result = {
        "version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "source_run": model,
        "inference_run": inference_run,
        "policy": "Most missed at 4m / confidence 0.35 against saved inference labels; "
                  "only previously inferred training chips, excluding live completed reviews. "
                  "Coverage is the saved inference cohort, not held-out performance; no density reranking. "
                  + ("Previously assigned chips are also excluded." if exclude_assigned else ""),
        "cities": {},
        "input_sha256": {},
    }
    if rank_by == "trees":
        result["policy"] = (
            "Retained training targets descending against saved inference labels; "
            "exclude live completed reviews and previously assigned chips when requested. "
            "No missed-count or density reranking; test and validation excluded."
        )
    result["rank_by"] = rank_by
    for city, review in REVIEWS.items():
        directory = root / "qa/registration" / review
        manifest_bytes = (directory / "manifest.json").read_bytes()
        manifest = json.loads(manifest_bytes)
        state = load_persisted_reviews(directory)
        done = {sid for sid, value in state["scene_reviews"].items() if value.get("done") or value.get("more_done")}
        explicit = {s.get("validation_chip_id") for s in manifest["scenes"]
                    if s["scene_id"] in done}
        done_trees = {str(s["tree_id"]) for s in manifest["samples"]
                      if s.get("scene_id") in done
                      and state["reviews"].get(s["sample_id"], {}).get("status")}
        cohort = root / "runs" / model / "evaluation" / f"train-{city}"
        paths = [cohort / name for name in
                 ("predictions.parquet", "ground-truth.parquet", "evaluation-metadata.json")]
        predictions, truth = [pd.read_parquet(p) for p in paths[:2]]
        config = json.loads(paths[2].read_text())["config"]
        assert set(truth.split) == {"train"}
        chips_path = root / "chips" / config['dataset'] / "chips.parquet"
        chips = pd.read_parquet(chips_path).set_index("chip_id")
        scale = config["imagery"]["resolution_m"] * config["targets"]["output_stride"]
        prediction_groups = dict(tuple(predictions[predictions.score >= .35].groupby('chip_id')))
        ranked = []
        for chip, targets in truth.groupby("chip_id"):
            if chip in prior.get(city, set()) or chip in explicit or set(targets.tree_id.astype(str)) <= done_trees:
                continue
            assert chips.loc[chip, "split"] == "train"
            pred = prediction_groups.get(chip, predictions.iloc[:0])
            scores = counts(match_frame(pred, targets, 4 / scale), len(targets)) if rank_by == "missed" else {}
            if rank_by == "missed" and not scores["missed"]:
                continue
            ranked.append({
                "chip_id": chip, "split": "train", "tag": tag,
                "trees": len(targets), "large": int((np.expm1(targets.dbh_log1p) >= 16).sum()),
                "collisions": int(chips.loc[chip, "collision_excluded_count"]),
                **scores,
                "reason": (f'{len(targets)} retained training targets (saved {model} labels)'
                           if rank_by == "trees" else
                           f'{scores["missed"]} missed of {len(targets)} targets at 4m / 0.35 (saved {model} labels)'),
                "median_offset_m": "n/a",
                "inference_run": f"{model}::train-{city}",
            })
        ranked.sort(key=lambda r: (-r[rank_by], r["chip_id"]))
        if len(ranked) < count:
            raise ValueError(f"Only {len(ranked)} eligible chips for {city}")
        result["cities"][city] = {
            "dataset": config['dataset'], "items": ranked[:count],
            "ranked_items": ranked,
            "eligible_chips": len(ranked), "scored_chips": int(truth.chip_id.nunique()),
            "review_revision": state["state_revision"],
            "previously_assigned_excluded": len(prior.get(city, set())),
        }
        for path in [*paths, chips_path]:
            result["input_sha256"][str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
        result["input_sha256"][str(directory / "manifest.json")] = hashlib.sha256(manifest_bytes).hexdigest()
    for path in assignment_paths:
        result["input_sha256"][str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = build(args.root, args.output)
    print(json.dumps({city: {"selected": len(data["items"]),
                            "eligible": data["eligible_chips"],
                            "missed_range": [data["items"][0]["missed"], data["items"][-1]["missed"]]}
                      for city, data in result["cities"].items()}))
