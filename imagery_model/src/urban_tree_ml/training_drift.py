"""Inference-only training curation shortlist; never a held-out performance report."""

import argparse
import hashlib
import json
import os
import shutil
import tarfile
from pathlib import Path

import numpy as np
import pandas as pd

MODEL = "sf-boston-naip-curation-v3-retry2"
SOURCE = "sf-boston-naip-curation-v3"
REVIEWS = {"ussfo": "ussfo-2022-mosaic", "usbos": "usbos-2023-external"}


def eligible(root, city):
    from urban_tree_ml.feedback import load_persisted_reviews

    directory = root / "qa/registration" / REVIEWS[city]
    manifest = json.loads((directory / "manifest.json").read_text())
    state = load_persisted_reviews(directory)
    done = {sid for sid, review in state["scene_reviews"].items() if review.get("done")}
    explicit = {s.get("validation_chip_id") for s in manifest["scenes"] if s["scene_id"] in done}
    done_trees = {
        str(s["tree_id"])
        for s in manifest["samples"]
        if s.get("scene_id") in done and state["reviews"].get(s["sample_id"], {}).get("status")
    }
    dataset = root / "chips" / f"{SOURCE}-{city}"
    chips = pd.read_parquet(dataset / "chips.parquet")
    labels = pd.read_parquet(dataset / "labels.parquet")
    groups = dict(tuple(labels[labels.split == "train"].groupby("chip_id")))
    rows = []
    for c in chips[chips.split == "train"].itertuples():
        trees = groups.get(c.chip_id)
        if (
            c.chip_id in explicit
            or trees is None
            or set(trees.tree_id.astype(str)) <= done_trees
            or not 8 <= c.candidate_tree_count <= 60
            or c.collision_excluded_count > 5
        ):
            continue
        large = int((np.expm1(trees.dbh_log1p) >= 16).sum())
        if large:
            rows.append(
                {
                    "chip_id": c.chip_id,
                    "trees": len(trees),
                    "large": large,
                    "collisions": int(c.collision_excluded_count),
                    "split": "train",
                }
            )
    return sorted(rows, key=lambda r: (-r["large"], r["chip_id"]))


def prepare(root, experiment):
    output = root / "run-inputs" / experiment
    output.mkdir(parents=True, exist_ok=False)
    payload = {
        "source_model": MODEL,
        "source_dataset": SOURCE,
        "cities": {},
        "policy": ("Up to 200 unreviewed train chips/city, 8-60 candidates, "
                   "<=5 collisions, DBH >=16in"),
    }
    for city in REVIEWS:
        candidates = eligible(root, city)
        payload["cities"][city] = {"eligible": len(candidates), "items": candidates[:200]}
    (output / "selection.json").write_text(json.dumps(payload, indent=2))
    (output / "FREEZE_COMPLETE").write_text("Candidate selection only; no annotation writes.\n")
    with tarfile.open(root / f"{experiment}-inputs.tar.gz", "w:gz") as archive:
        archive.add(output, arcname=experiment)
    print({city: (v["eligible"], len(v["items"])) for city, v in payload["cities"].items()})


def infer(root, experiment):
    from urban_tree_ml.config import ProjectConfig
    from urban_tree_ml.evaluation import run_evaluation

    selection = json.loads((root / "run-inputs" / experiment / "selection.json").read_text())
    run = root / "runs" / experiment
    run.mkdir(parents=True, exist_ok=False)
    checkpoint = root / "runs" / MODEL / "checkpoints/035.ckpt"
    if any((checkpoint.parent.parent / 'evaluation' / f'train-{c}').exists()
           for c in selection['cities']):
        raise ValueError('Training inference already exists; do not overwrite saved predictions')
    provenance = {
        "inference_only": True,
        "source_model": MODEL,
        "checkpoint_sha256": hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
    }
    (run / "inference-provenance.json").write_text(json.dumps(provenance, indent=2))
    for city, entry in selection["cities"].items():
        config = ProjectConfig.model_validate_json(
            (root / "runs" / MODEL / f"config-{city}.json").read_text()
        )
        source = root / "chips" / config.dataset
        ids = {r["chip_id"] for r in entry["items"]}
        destination = root / "chips" / f"{experiment}-{city}"
        destination.mkdir(parents=True, exist_ok=False)
        for name in ["chips.parquet", "labels.parquet"]:
            data = pd.read_parquet(source / name)
            data = data[(data.split == "train") & data.chip_id.isin(ids)].copy()
            assert set(data.split) == {"train"} and set(data.chip_id) == ids
            if name == "chips.parquet":
                data["path"] = data.path.map(lambda p, name=source.name: f"../{name}/{p}")
            data.to_parquet(destination / name, index=False)
        for name in ["normalization.json", "summary.json"]:
            shutil.copy2(source / name, destination / name)
        config.dataset = destination.name
        config.experiment = experiment
        (run / f"config-{city}.json").write_text(config.model_dump_json(indent=2))
        print(f"Inference only: {city}, {len(ids)} training chips", flush=True)
        run_evaluation(
            config, checkpoint, split="train", device_name="cuda", cohort=f"train-{city}"
        )
    (run / "COMPLETE").write_text("Training-chip inference only; no training or test evaluation.\n")


def offset_candidates(predictions, truth, meters_per_cell):
    """Mutual nearest neighbors reduce ambiguous many-to-one crown associations."""
    large = np.expm1(truth.dbh_log1p.to_numpy()) >= 16
    predictions = predictions[predictions.score >= 0.35]
    if predictions.empty or truth.empty:
        return []
    t = truth[["output_x", "output_y"]].to_numpy()
    p = predictions[["output_x", "output_y"]].to_numpy()
    distance = np.linalg.norm(t[:, None, :] - p[None, :, :], axis=2) * meters_per_cell
    nearest = distance.argmin(axis=1)
    reverse = distance.argmin(axis=0)
    rows = []
    for i, j in enumerate(nearest):
        d = float(distance[i, j])
        if large[i] and reverse[j] == i and 3 <= d <= 12:
            rows.append(
                {
                    "tree_id": str(truth.iloc[i].tree_id),
                    "distance_m": round(d, 2),
                    "dbh_in": round(float(np.expm1(truth.iloc[i].dbh_log1p)), 1),
                    "score": float(predictions.iloc[j].score),
                }
            )
    return sorted(rows, key=lambda r: -r["distance_m"])


def shortlist(root, experiment):
    run = root / "runs" / experiment
    if not (run / "COMPLETE").exists():
        raise ValueError("Inference not complete")
    frozen = json.loads((root / "run-inputs" / experiment / "selection.json").read_text())
    result = {
        "version": 1,
        "source_run": MODEL,
        "inference_run": experiment,
        "policy": ("Unreviewed train chips, DBH >=16in, mutual-nearest prediction "
                   "3-12m away at score >=.35; hypotheses, not confirmed offsets"),
        "cities": {},
        "input_sha256": {},
    }
    for city in REVIEWS:
        live = {r["chip_id"]: r for r in eligible(root, city)}
        # Evaluator stores new cohorts beside the checkpoint, not config.experiment.
        cohort = root / "runs" / MODEL / "evaluation" / f"train-{city}"
        predictions = pd.read_parquet(cohort / "predictions.parquet")
        truth = pd.read_parquet(cohort / "ground-truth.parquet")
        meta = json.loads((cohort / "evaluation-metadata.json").read_text())
        config = meta["config"]
        scale = config["imagery"]["resolution_m"] * config["targets"]["output_stride"]
        groups = dict(tuple(predictions.groupby("chip_id")))
        ranked = []
        for chip, trees in truth.groupby("chip_id"):
            if chip not in live or chip not in groups:
                continue
            offsets = offset_candidates(groups[chip], trees, scale)
            if not offsets:
                continue
            ranked.append(
                {
                    **live[chip],
                    "tag": "large-tree offsets",
                    "offset_count": len(offsets),
                    "offsets": offsets,
                    "median_offset_m": round(
                        float(np.median([r["distance_m"] for r in offsets])), 2
                    ),
                    "reason": f"{len(offsets)} large-tree offset candidates (3-12 m)",
                    "inference_run": f"{MODEL}::train-{city}",
                }
            )
        ranked.sort(key=lambda r: (-r["offset_count"], -r["median_offset_m"], r["chip_id"]))
        if len(ranked) < 10:
            raise ValueError(f"Only {len(ranked)} actionable chips for {city}; do not pad the list")
        result["cities"][city] = {
            "dataset": f"{SOURCE}-{city}",
            "items": ranked[:10],
            "scored_chips": len(frozen["cities"][city]["items"]),
            "qualified_chips": len(ranked),
        }
        for name in ["predictions.parquet", "ground-truth.parquet", "evaluation-metadata.json"]:
            path = cohort / name
            result["input_sha256"][str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    output = root / "review-queues" / f"{experiment}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x") as stream:
        json.dump(result, stream, indent=2)
    print(json.dumps({city: value["items"] for city, value in result["cities"].items()}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--experiment", required=True)
    parser.add_argument("--root", type=Path, default=Path(os.environ.get("TREE_ML_DATA_ROOT", ".")))
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--shortlist", action="store_true")
    parser.add_argument("configs", nargs="*")
    args = parser.parse_args()
    if not args.experiment or any(
        c not in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in args.experiment
    ):
        parser.error("Invalid experiment")
    (prepare if args.prepare else shortlist if args.shortlist else infer)(
        args.root, args.experiment
    )
