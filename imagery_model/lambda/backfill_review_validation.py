"""Backfill validation review from a completed checkpoint and its frozen city inputs."""

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

import polars as pl
import torch

from urban_tree_ml.chips import build_chips
from urban_tree_ml.config import ProjectConfig
from urban_tree_ml.evaluation import run_evaluation


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def local_imagery(root, source):
    # Archived absolute paths can name a different workstation or Linux host.
    relative = Path(str(source).replace("\\", "/").split("/imagery/", 1)[1])
    path = root / "imagery" / relative
    if not path.is_file():
        raise FileNotFoundError(path)
    return path


def describe_review_output(result, target, run, source_dataset, normalization):
    """Keep the checkpoint and published file references valid after moving out of staging."""
    metrics = read(result / "metrics.json")
    for key, file in [
        ("predictions", "predictions.parquet"),
        ("matches", "matches.parquet"),
        ("ground_truth", "ground-truth.parquet"),
        ("taxonomy", "taxonomy.json"),
        ("evaluation_metadata", "evaluation-metadata.json"),
    ]:
        metrics[key] = str(target / file)
    metrics.update(
        review_only=True,
        run_id=run,
        source_dataset=source_dataset,
        normalization_source=str(normalization),
    )
    write(result / "metrics.json", metrics)
    for filename in ("evaluation-metadata.json", "inference-coverage.json"):
        document = read(result / filename)
        document.update(review_only=True, run_id=run, source_dataset=source_dataset)
        write(result / filename, document)


def backfill(root, run, checkpoint, checkpoint_hash, city, stage):
    run_dir = root / "runs" / run
    frozen = root / "run-inputs" / run / city
    cfg_path = run_dir / f"config-{city}.json"
    cfg = ProjectConfig.model_validate_json(cfg_path.read_text())
    receipt = run_dir / "curated-validation.json"
    policy = read(receipt)["cities"][city]
    missing = set(policy["excluded_validation_chips"])
    if not missing:
        return
    (cohort,) = (
        p.parent
        for p in (run_dir / "evaluation").glob("*/metrics.json")
        if not p.parent.name.startswith("parent-")
        and read(p).get("split") == "validation"
        and read(p.parent / "evaluation-metadata.json")["config"]["inventory"]["city"].lower()
        == city
    )
    existing = {row["chip_id"] for row in read(cohort / "inference-coverage.json")["chips"]}
    if (
        existing != set(policy["chip_ids"])
        or existing & missing
        or len(existing | missing) != policy["original_validation_chips"]
    ):
        raise ValueError("Original grid and evaluated coverage disagree")
    target = root / "review-inference" / run / "evaluation" / cohort.name
    normalization = root / "chips" / cfg.dataset / "normalization.json"
    raster = local_imagery(root, cfg.imagery.local_raster)
    geometry = read(run_dir / "resolution-audit.json").get(city)
    feedback = frozen / "training-feedback.json"
    inventory = frozen / "inventory.parquet"
    identity = dict(
        run=run,
        cohort=cohort.name,
        city=city,
        checkpoint=checkpoint.name,
        checkpoint_sha256=checkpoint_hash,
        config_sha256=digest(cfg_path),
        validation_grid_sha256=digest(receipt),
        taxonomy_sha256=digest(cohort / "taxonomy.json"),
        normalization_sha256=digest(normalization),
        feedback_sha256=digest(feedback),
        inventory_sha256=digest(inventory),
        source_raster=str(raster),
        device="cpu",
        precision="fp32",
    )
    if target.exists():
        if (
            read(target / "review-provenance.json") != identity
            or {r["chip_id"] for r in read(target / "inference-coverage.json")["chips"]} != missing
        ):
            raise ValueError(f"Existing review export differs at {target}")
        print(f"{city}: already published", flush=True)
        return
    work = stage / city
    data = work / "chips" / "review"
    cfg.paths.root = work
    cfg.imagery.local_raster = raster
    cfg.imagery.boundary_shifts = False
    cfg.reference.taxonomy_path = cohort / "taxonomy.json"
    cfg.reference.normalization_path = normalization
    cfg.dataset = read(feedback)["dataset"]
    work.mkdir(parents=True, exist_ok=True)
    stamp = work / "materialized.json"
    if not stamp.exists() or read(stamp) != identity:
        print(f"{city}: materializing {len(missing)} validation windows", flush=True)
        build_chips(
            cfg,
            raster,
            feedback_path=feedback,
            inventory_source=inventory,
            feedback_source_raster=local_imagery(root, geometry["source_raster"])
            if geometry
            else None,
            output_dataset="review",
            allowed_chip_ids=missing,
            allowed_splits={"validation"},
            validation_supervision_only=True,
            validation_full_targets=True,
        )
        shutil.copyfile(normalization, data / "normalization.json")
        write(data / "summary.json", dict(source_raster=str(raster)))
        write(stamp, identity)
    cfg.dataset = "review"
    cfg.training.batch_size = 1
    cfg.training.workers = 0
    print(f"{city}: running checkpoint on {len(missing)} chips", flush=True)
    run_evaluation(
        cfg, checkpoint, split="validation", device_name="cpu", cohort=cohort.name, output_run=work
    )
    result = work / "evaluation" / cohort.name
    coverage = read(result / "inference-coverage.json")
    ids = [r["chip_id"] for r in coverage["chips"]]
    if not coverage["complete"] or set(ids) != missing or len(ids) != len(missing):
        raise ValueError("Incomplete backfill coverage")
    predictions = pl.read_parquet(result / "predictions.parquet", columns=["chip_id", "score"])
    if (
        set(predictions["chip_id"]) - missing
        or predictions.filter(~pl.col("score").is_between(0, 1).fill_null(False)).height
    ):
        raise ValueError("Invalid inference export")
    describe_review_output(result, target, run, normalization.parent.name, normalization)
    write(result / "review-provenance.json", identity)
    target.parent.mkdir(exist_ok=True, parents=True)
    # Publish the verified city in one rename on the same filesystem.
    os.rename(result, target)
    print(f"{city}: published {len(ids)} chips, {len(predictions)} predictions", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--run", required=True)
    parser.add_argument("--checkpoint-sha256", required=True)
    parser.add_argument("--cities", nargs="+", required=True)
    parser.add_argument("--work-dir", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    run_dir = root / "runs" / args.run
    if not (run_dir / "COMPLETE").is_file():
        raise ValueError("Requires a completed run")
    checkpoint = (
        run_dir
        / "checkpoints"
        / Path(read(run_dir / "training-result.json")["best_checkpoint"]).name
    )
    checkpoint_hash = digest(checkpoint)
    if checkpoint_hash != args.checkpoint_sha256:
        raise ValueError("Selected checkpoint hash differs")
    torch.set_num_threads(min(8, torch.get_num_threads()))
    scratch = root / "review-inference"
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=scratch) as temp:
        for city in args.cities:
            backfill(root, args.run, checkpoint, checkpoint_hash, city, args.work_dir or Path(temp))
