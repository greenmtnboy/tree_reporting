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
        summary_path = directory / 'summary.json'
        if summary_path.exists() and json.loads(summary_path.read_text()).get('label_status') == 'unavailable':
            raise ValueError('Prediction-only imagery must be curated and rebuilt before joint training')
        if city.lower() == 'usffx':
            from urban_tree_ml.fairfax_curation import verify_dataset
            verify_dataset(directory)
        frame = pd.read_parquet(directory / "chips.parquet")
        if frame.chip_id.duplicated().any() or not set(frame.split) <= {
            "train", "validation", "test"
        }:
            raise ValueError(f"invalid chip identities or splits in {city}")
        temporal = (directory/'temporal-dataset.json').exists()
        if temporal:
            receipt = json.loads((directory/'temporal-dataset.json').read_text())
            config = ProjectConfig.model_validate(receipt['config'])
            from urban_tree_ml.temporal_training import verify_dataset
            verify_dataset(directory, config)
            if config.imagery.acquisition_id != city:
                raise ValueError('Joint temporal source identity differs')
        if not temporal and not {"train", "validation"} <= set(frame.split):
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
    if 'usffx' in sources:
        summary['fairfax_source'] = str(sources['usffx'].resolve())
    (destination / "summary.json").write_text(json.dumps(summary, indent=2))
    return summary


def run(config_paths: list[str], experiment: str, prepared_run: str | None = None) -> None:
    from urban_tree_ml.chips import build_chips
    from urban_tree_ml.feedback import _json_sha256, load_persisted_reviews

    configs = [load_config(path) for path in config_paths]
    root = configs[0].paths.root
    nyc_policy = None
    if (root/'run-inputs'/experiment/'curated-nyc-joint.json').exists():
        if prepared_run:
            raise ValueError('Curated NYC joint run requires a fresh curation build')
        from urban_tree_ml.curated_nyc_joint import validate
        nyc_policy, nyc_config = validate(root, experiment)
        configs.append(nyc_config)
    fairfax_path = root/'run-inputs'/experiment/'fairfax-joint.json'
    if fairfax_path.exists():
        from urban_tree_ml.prepared_finetune import config_from_recipe
        payload = json.loads(fairfax_path.read_text())
        if prepared_run or payload.get('test_evaluated') is not False:
            raise ValueError('Fairfax requires fresh frozen development inputs')
        configs.append(config_from_recipe(root,payload['config'],experiment))
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
    base_path = warm_path.with_name('base-start.json')
    if warm_path.exists() and base_path.exists():
        raise ValueError('Base initialization cannot also load tree-model weights')
    recipe_path = base_path if base_path.exists() else warm_path
    if recipe_path.exists():
        warm = json.loads(recipe_path.read_text())
        if base_path.exists():
            from urban_tree_ml.capacity_run import validate_base_recipe
            validate_base_recipe(warm)
        from urban_tree_ml.warm_recipe import apply_warm_recipe
        for config in configs:
            apply_warm_recipe(config, warm)
    run_dir = root / "runs" / experiment
    resolution_path = root / 'run-inputs' / experiment / 'resolution-policy.json'
    resolution_audit = {}
    if resolution_path.exists():
        if prepared_run:
            raise ValueError('30 cm recipe cannot reuse prepared chips')
        if json.loads(resolution_path.read_text()) != {'recipe': 'swin-native30-upsample60-v1'}:
            raise ValueError('Unknown resolution recipe')
        from urban_tree_ml.resolution_recipe import apply_resolution_recipe
        for config in configs:
            if config.inventory.city.lower() == 'usffx' or (nyc_policy and config.inventory.city.lower() == 'usnyc'):
                continue  # Already native 30 cm; do not route through the 60 cm regrid.
            resolution_audit[config.inventory.city.lower()] = apply_resolution_recipe(config, experiment)
    if recipe_path.exists():
        from urban_tree_ml.warm_recipe import validate_warm_grid
        for config in configs:
            apply_warm_recipe(config, warm)
            validate_warm_grid(config, warm)
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
    if recipe_path.exists() and warm.get('vegetation_groups') is True:
        from urban_tree_ml.vegetation_groups import expanded_taxonomy
        expanded = expanded_taxonomy(vocabulary)
        expanded_path = run_dir/'vegetation-taxonomy.json'
        expanded_path.write_text(json.dumps(expanded, indent=2))
        reference.taxonomy_path = expanded_path  # never rewrite frozen input taxonomy
    if resolution_audit:
        (run_dir / 'resolution-audit.json').write_text(json.dumps(resolution_audit, indent=2))
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
            feedback_source_raster=(Path(resolution_audit[city]['source_raster'])
                                    if city in resolution_audit else None),
            allowed_chip_ids=(set(nyc_policy['train_chip_ids']) if nyc_policy and city == 'usnyc' else None),
            allowed_splits=({'train'} if city == 'usnyc' else {'train','validation'}) if nyc_policy else None,
        )
        if nyc_policy and city == 'usnyc':
            from urban_tree_ml.curated_nyc_joint import pin_validation_and_audit
            pin_validation_and_audit(root, experiment, root/'chips'/dataset, nyc_policy, config=config)
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
    from urban_tree_ml.curated_validation import restrict_sources
    restrict_sources(root/'run-inputs'/experiment, sources, run_dir)
    from urban_tree_ml.temporal_training import build_sources
    temporal_configs, temporal_sources = build_sources(root, experiment, reference,
        warm if recipe_path.exists() else {}, run_dir)
    configs.extend(temporal_configs)
    sources.update(temporal_sources)
    print(json.dumps(combine_manifests(sources, root / "chips" / experiment)), flush=True)
    if curated_path.exists():
        restrict_joint(root, experiment, policy)
    train_and_evaluate(configs, experiment, run_dir, training_inference=not base_path.exists())


def train_and_evaluate(configs, dataset, run_dir, *, training_inference=True):
    from urban_tree_ml.evaluation import run_evaluation
    from urban_tree_ml.training import run_training

    training_config = configs[0].model_copy(deep=True)
    training_config.dataset = dataset
    from urban_tree_ml.curated_validation import load_policy, verify_cohorts
    inputs = training_config.paths.root/'run-inputs'/training_config.experiment
    if load_policy(inputs) is not None:
        cohorts = {}
        for config in configs:
            if config.imagery.acquisition_id is not None:
                from urban_tree_ml.temporal_training import verify_dataset
                verify_dataset(config.paths.root/'chips'/config.dataset, config)
            frame = pd.read_parquet(config.paths.root/'chips'/config.dataset/'chips.parquet')
            ids = set(frame.loc[frame.split.eq('validation'),'chip_id'])
            if ids or config.imagery.acquisition_id is None:
                cohorts[config.imagery.acquisition_id or config.inventory.city.lower()] = ids
        verify_cohorts(training_config.paths.root, training_config.experiment, cohorts)
    warm_path = training_config.paths.root / "run-inputs" / training_config.experiment / "warm-start.json"
    if warm_path.exists():
        warm = json.loads(warm_path.read_text())
        checkpoint = training_config.paths.root / warm["checkpoint"]
        if hashlib.sha256(checkpoint.read_bytes()).hexdigest() != warm["checkpoint_sha256"]:
            raise ValueError("Warm-start checkpoint hash mismatch")
        parent_taxonomy = checkpoint.parents[1] / "evaluation/validation/taxonomy.json"
        parent = json.loads(parent_taxonomy.read_text())
        candidate = json.loads(training_config.reference.taxonomy_path.read_text())
        migration = None
        if warm.get('vegetation_groups') is True:
            from urban_tree_ml.vegetation_groups import expanded_taxonomy
            if candidate != expanded_taxonomy(parent):
                raise ValueError('Invalid append-only vegetation vocabulary')
            migration = parent
        elif parent != candidate:
            raise ValueError("Warm-start vocabulary differs; explicit head migration required")
        result = run_training(training_config, initial_checkpoint=str(checkpoint), parent_taxonomy=migration)
    else:
        result = run_training(training_config)
    (run_dir / "training-result.json").write_text(json.dumps(result, indent=2))
    for config in configs:
        city = config.imagery.acquisition_id or config.inventory.city.lower()
        if config.imagery.acquisition_id and not pd.read_parquet(
                config.paths.root/'chips'/config.dataset/'chips.parquet').split.eq('validation').any():
            continue
        print(f"Evaluating validation only: {city}", flush=True)
        run_evaluation(
            config, checkpoint_path=result["best_checkpoint"], split="validation",
            cohort="validation" if city == "ussfo" else f"validation-{city}", device_name="cuda",
        )
    # Re-run the unchanged parent through this exact decoder and frozen inputs.
    # Historical cached peaks can differ after an inference bug fix (e.g. ties).
    # Keep these additional cohorts inside the new run, never overwrite its parent.
    if warm_path.exists() and parent == candidate:
        paired_cohorts = {}
        for config in configs:
            city = config.imagery.acquisition_id or config.inventory.city.lower()
            if config.imagery.acquisition_id and not pd.read_parquet(
                    config.paths.root/'chips'/config.dataset/'chips.parquet').split.eq('validation').any():
                continue
            cohort = "validation" if city == "ussfo" else f"validation-{city}"
            print(f"Paired parent validation inference: {city}", flush=True)
            run_evaluation(config, checkpoint_path=checkpoint, split="validation",
                           cohort="parent-"+cohort, device_name="cuda", output_run=run_dir)
            paired_cohorts[cohort] = "parent-"+cohort
        (run_dir / "paired-parent-validation.json").write_text(json.dumps({
            "parent_run": checkpoint.parents[1].name,
            "checkpoint_sha256": warm["checkpoint_sha256"],
            "cohorts": paired_cohorts, "same_decoder_and_inputs": True,
            "test_evaluated": False}, indent=2))
    # Keep the GPU alive for reviewer overlays. These are diagnostics, not holdouts.
    # run_evaluation verifies exact processed-chip coverage, including zero detections.
    for config in (configs if training_inference else []):
        city = config.imagery.acquisition_id or config.inventory.city.lower()
        if config.imagery.acquisition_id and not pd.read_parquet(
                config.paths.root/'chips'/config.dataset/'chips.parquet').split.eq('train').any():
            continue
        print(f"Batch inference over all training chips: {city}", flush=True)
        run_evaluation(
            config, checkpoint_path=result["best_checkpoint"], split="train",
            cohort=f"train-{city}", device_name="cuda",
        )
    (run_dir / "COMPLETE").write_text(
        "Training and all city validation evaluations completed. "
        + ("Full training-chip inference completed.\n" if training_inference else "Training inference not requested.\n")
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--experiment", required=True)
    parser.add_argument("--prepared-run")
    parser.add_argument("configs", nargs="+")
    args = parser.parse_args()
    run(args.configs, args.experiment, args.prepared_run)
