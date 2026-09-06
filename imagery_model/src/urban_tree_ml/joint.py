"""Freeze curation, build independent city chips, and train a shared model."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd

from urban_tree_ml.config import ReferenceConfig, load_config


def combine_manifests(sources: dict[str, Path], destination: Path) -> dict:
    """Keep city identity and original spatial splits; use portable relative paths."""
    frames = []
    normalizations = []
    counts = {}
    for city, directory in sources.items():
        frame = pd.read_parquet(directory / "chips.parquet")
        if frame.chip_id.duplicated().any() or not set(frame.split) <= {
            "train", "validation", "test"
        }:
            raise ValueError(f"invalid chip identities or splits in {city}")
        if not {"train", "validation"} <= set(frame.split):
            raise ValueError(f"{city} needs both train and validation chips")
        counts[city] = frame.groupby("split").size().to_dict()
        frame["source_chip_id"] = frame.chip_id
        frame["city"] = city
        frame["chip_id"] = city + ":" + frame.chip_id
        frame["path"] = frame.path.map(
            lambda value, name=directory.name: (Path("..") / name / value).as_posix()
        )
        frames.append(frame)
        normalizations.append(json.loads((directory / "normalization.json").read_text()))
    if any(
        n[key] != normalizations[0][key]
        for n in normalizations for key in ("mean", "std")
    ):
        raise ValueError("joint cities must use identical normalization")
    destination.mkdir(parents=True, exist_ok=True)
    pd.concat(frames, ignore_index=True).to_parquet(destination / "chips.parquet", index=False)
    (destination / "normalization.json").write_text(json.dumps(normalizations[0], indent=2))
    summary = {"cities": counts, "test_evaluated": False}
    (destination / "summary.json").write_text(json.dumps(summary, indent=2))
    return summary


def run(config_paths: list[str], experiment: str) -> None:
    from urban_tree_ml.chips import build_chips
    from urban_tree_ml.evaluation import run_evaluation
    from urban_tree_ml.feedback import _json_sha256, load_persisted_reviews
    from urban_tree_ml.training import run_training

    configs = [load_config(path) for path in config_paths]
    root = configs[0].paths.root
    run_dir = root / "runs" / experiment
    if run_dir.exists():
        raise ValueError(f"run already exists: {run_dir}")
    reference = ReferenceConfig(
        taxonomy_path=root / "run-inputs" / experiment / "ussfo" / "taxonomy.json",
        normalization_path=root / "chips" / configs[0].dataset / "normalization.json",
    )
    vocabulary = json.loads(reference.taxonomy_path.read_text())
    audit = {}
    # Validate every frozen input before any expensive materialization.
    for config in configs:
        city = config.inventory.city.lower()
        if config.paths.root != root or city in audit:
            raise ValueError("cities must be distinct and share an artifact root")
        directory = root / "run-inputs" / experiment / city
        if json.loads((directory / "taxonomy.json").read_text()) != vocabulary:
            raise ValueError(f"taxonomy differs for {city}")
        manifest = json.loads((directory / "manifest.json").read_text())
        state = load_persisted_reviews(directory)
        feedback = json.loads((directory / "training-feedback.json").read_text())
        if feedback["source_reviews_sha256"] != state["state_revision"]:
            raise ValueError(f"unpublished curation for {city}")
        if feedback["source_manifest_sha256"] != _json_sha256(manifest):
            raise ValueError(f"stale curation manifest for {city}")
        audit[city] = {
            "review_revision": state["state_revision"],
            "scenes": len(manifest["scenes"]),
            "done": sum(bool(r.get("done")) for r in state["scene_reviews"].values()),
            "tree_reviews": len(state["reviews"]),
            "exclusions": len(feedback["exclusions"]),
            "point_corrections": len(feedback["point_corrections"]),
            "regions": len(feedback.get("region_overrides", [])),
            "inventory_sha256": hashlib.sha256(
                (directory / "inventory.parquet").read_bytes()
            ).hexdigest(),
        }
    run_dir.mkdir(parents=True)
    (run_dir / "curation-audit.json").write_text(json.dumps(audit, indent=2))
    sources = {}
    for config in configs:
        city = config.inventory.city.lower()
        config.reference = reference
        dataset = f"{experiment}-{city}"
        print(f"Building {city} from frozen curation", flush=True)
        summary = build_chips(
            config, config.imagery.local_raster,
            feedback_path=root / "run-inputs" / experiment / city / "training-feedback.json",
            output_dataset=dataset,
            inventory_source=root / "run-inputs" / experiment / city / "inventory.parquet",
        )
        print(json.dumps(summary), flush=True)
        config.dataset = dataset
        config.experiment = experiment
        sources[city] = root / "chips" / dataset
        (run_dir / f"config-{city}.json").write_text(config.model_dump_json(indent=2))
    print(json.dumps(combine_manifests(sources, root / "chips" / experiment)), flush=True)
    training_config = configs[0].model_copy(deep=True)
    training_config.dataset = experiment
    result = run_training(training_config)
    (run_dir / "training-result.json").write_text(json.dumps(result, indent=2))
    for config in configs:
        city = config.inventory.city.lower()
        print(f"Evaluating validation only: {city}", flush=True)
        run_evaluation(
            config, checkpoint_path=result["best_checkpoint"], split="validation",
            cohort="validation" if city == "ussfo" else f"validation-{city}", device_name="cuda",
        )
    (run_dir / "COMPLETE").write_text("Training and both validation evaluations completed.\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--experiment", required=True)
    parser.add_argument("configs", nargs="+")
    args = parser.parse_args()
    run(args.configs, args.experiment)
