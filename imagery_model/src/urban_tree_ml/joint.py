"""Freeze curation, build independent city chips, and train a shared model."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import date
from pathlib import Path

import pandas as pd

from urban_tree_ml.config import ProjectConfig, ReferenceConfig, load_config
from urban_tree_ml.prepare_next_run import validate_encoding
from urban_tree_ml.taxonomy import Taxonomy


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


def run(config_paths: list[str], experiment: str, prepared_run: str | None = None) -> None:
    from urban_tree_ml.chips import build_chips
    from urban_tree_ml.feedback import _json_sha256, load_persisted_reviews

    configs = [load_config(path) for path in config_paths]
    root = configs[0].paths.root
    center_path = root / 'run-inputs' / experiment / 'center-policy.json'
    if center_path.exists():
        from urban_tree_ml.config import TargetsConfig
        policy = json.loads(center_path.read_text())
        for config in configs:
            config.targets = TargetsConfig.model_validate({**config.targets.model_dump(), **policy})
    crown_path = root / 'run-inputs' / experiment / 'crown-head.json'
    if crown_path.exists():
        for config in configs:
            config.model.crown_head = bool(json.loads(crown_path.read_text())['enabled'])
    dates_path = root / 'run-inputs' / experiment / 'imagery-dates.json'
    if dates_path.exists():
        dates = json.loads(dates_path.read_text())
        for config in configs:
            cutoff = dates[config.inventory.city.lower()]['cutoff']
            config.imagery.planting_date_cutoff = date.fromisoformat(cutoff) if cutoff else None
    warm_path = root / "run-inputs" / experiment / "warm-start.json"
    if warm_path.exists():
        warm = json.loads(warm_path.read_text())
        from urban_tree_ml.warm_recipe import apply_warm_recipe
        for config in configs:
            apply_warm_recipe(config, warm)
    run_dir = root / "runs" / experiment
    if run_dir.exists():
        raise ValueError(f"run already exists: {run_dir}")
    if prepared_run:
        if Path(prepared_run).name != prepared_run or prepared_run in {".", ".."}:
            raise ValueError("prepared run must be a run name")
        previous = root / "runs" / prepared_run
        configs = [ProjectConfig.model_validate_json(path.read_text())
                   for path in sorted(previous.glob("config-*.json"))]
        if len(configs) != 2 or {c.inventory.city.lower() for c in configs} != {"ussfo", "usbos"}:
            raise ValueError("prepared run must contain both city configs")
        for config in configs:
            if config.paths.root != root:
                raise ValueError("prepared artifact root differs")
            directory = root / "chips" / config.dataset
            frame = pd.read_parquet(directory / "chips.parquet")
            if not all((directory / path).is_file() for path in frame.path):
                raise ValueError(f"missing prepared chips: {config.dataset}")
            Taxonomy(**json.loads(config.reference.taxonomy_path.read_text()))
            config.experiment = experiment
        # Copy provenance only; reuse immutable prepared pixels and split assignments.
        shutil.copytree(root / "run-inputs" / prepared_run, root / "run-inputs" / experiment)
        run_dir.mkdir(parents=True)
        shutil.copy2(previous / "curation-audit.json", run_dir / "curation-audit.json")
        (run_dir / "recovery.json").write_text(json.dumps({"prepared_run": prepared_run}))
        for config in configs:
            (run_dir / f"config-{config.inventory.city.lower()}.json").write_text(
                config.model_dump_json(indent=2)
            )
        train_and_evaluate(configs, prepared_run, run_dir)
        return
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
        validate_encoding(pd.read_parquet(directory / "inventory.parquet"), Taxonomy(**vocabulary))
        manifest = json.loads((directory / "manifest.json").read_text())
        # New freezes carry a byte-bound publication receipt, independent of PROJ
        # rounding on the training host. Preserve strict legacy validation.
        if (directory / 'integrity.json').exists():
            from urban_tree_ml.freeze_next_run import validate_frozen_city
            state = validate_frozen_city(directory)
        else:
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
    curated_path = root / 'run-inputs' / experiment / 'curated-only.json'
    if curated_path.exists():
        from urban_tree_ml.curated_only import pin_validation, restrict_joint
        policy = json.loads(curated_path.read_text())
        pin_validation(root, sources, policy)
    print(json.dumps(combine_manifests(sources, root / "chips" / experiment)), flush=True)
    if curated_path.exists():
        restrict_joint(root, experiment, policy)
    train_and_evaluate(configs, experiment, run_dir)


def train_and_evaluate(configs, dataset, run_dir, *, training_inference=True):
    from urban_tree_ml.evaluation import run_evaluation
    from urban_tree_ml.training import run_training

    training_config = configs[0].model_copy(deep=True)
    training_config.dataset = dataset
    warm_path = training_config.paths.root / "run-inputs" / training_config.experiment / "warm-start.json"
    if warm_path.exists():
        warm = json.loads(warm_path.read_text())
        checkpoint = training_config.paths.root / warm["checkpoint"]
        if hashlib.sha256(checkpoint.read_bytes()).hexdigest() != warm["checkpoint_sha256"]:
            raise ValueError("Warm-start checkpoint hash mismatch")
        parent_taxonomy = checkpoint.parents[1] / "evaluation/validation/taxonomy.json"
        if json.loads(parent_taxonomy.read_text()) != json.loads(training_config.reference.taxonomy_path.read_text()):
            raise ValueError("Warm-start vocabulary differs; explicit head migration required")
        result = run_training(training_config, initial_checkpoint=str(checkpoint))
    else:
        result = run_training(training_config)
    (run_dir / "training-result.json").write_text(json.dumps(result, indent=2))
    for config in configs:
        city = config.inventory.city.lower()
        print(f"Evaluating validation only: {city}", flush=True)
        run_evaluation(
            config, checkpoint_path=result["best_checkpoint"], split="validation",
            cohort="validation" if city == "ussfo" else f"validation-{city}", device_name="cuda",
        )
    # Keep the GPU alive for reviewer overlays. These are diagnostics, not holdouts.
    # run_evaluation verifies exact processed-chip coverage, including zero detections.
    for config in (configs if training_inference else []):
        city = config.inventory.city.lower()
        print(f"Batch inference over all training chips: {city}", flush=True)
        run_evaluation(
            config, checkpoint_path=result["best_checkpoint"], split="train",
            cohort=f"train-{city}", device_name="cuda",
        )
    (run_dir / "COMPLETE").write_text(
        "Training and both validation evaluations completed. "
        + ("Full training-chip inference completed.\n" if training_inference else "Training inference not requested.\n")
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--experiment", required=True)
    parser.add_argument("--prepared-run")
    parser.add_argument("configs", nargs="+")
    args = parser.parse_args()
    run(args.configs, args.experiment, args.prepared_run)
