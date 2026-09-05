from __future__ import annotations

import json
import math
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from urban_tree_ml.config import ProjectConfig, taxonomy_path

_COHORT_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def _greedy_matches(
    predictions: pd.DataFrame,
    ground_truth: pd.DataFrame,
    *,
    radius_output_px: float,
) -> list[tuple[int, int, float]]:
    """Match scored predictions to the nearest unused truth in the same chip."""
    matched_truth: set[int] = set()
    matches: list[tuple[int, int, float]] = []
    ordered = predictions.sort_values("score", ascending=False, kind="stable")
    truths_by_chip = {
        str(chip_id): group for chip_id, group in ground_truth.groupby("chip_id", sort=False)
    }
    for prediction_index, prediction in ordered.iterrows():
        candidates = truths_by_chip.get(str(prediction["chip_id"]))
        if candidates is None:
            continue
        best_index: int | None = None
        best_distance = math.inf
        for truth_index, truth in candidates.iterrows():
            if int(truth_index) in matched_truth:
                continue
            distance = math.hypot(
                float(prediction["output_x"]) - float(truth["output_x"]),
                float(prediction["output_y"]) - float(truth["output_y"]),
            )
            if distance <= radius_output_px and distance < best_distance:
                best_index = int(truth_index)
                best_distance = distance
        if best_index is not None:
            matched_truth.add(best_index)
            matches.append((int(prediction_index), best_index, best_distance))
    return matches


def _average_precision(
    predictions: pd.DataFrame,
    ground_truth: pd.DataFrame,
    *,
    radius_output_px: float,
) -> float | None:
    if ground_truth.empty:
        return None
    ordered = predictions.sort_values("score", ascending=False, kind="stable")
    matched_prediction_ids = {
        prediction_index
        for prediction_index, _, _ in _greedy_matches(
            ordered, ground_truth, radius_output_px=radius_output_px
        )
    }
    true_positive = np.asarray(
        [int(index in matched_prediction_ids) for index in ordered.index], dtype=np.float64
    )
    false_positive = 1.0 - true_positive
    recall = np.cumsum(true_positive) / len(ground_truth)
    precision = np.cumsum(true_positive) / np.maximum(
        np.cumsum(true_positive) + np.cumsum(false_positive), 1.0
    )
    recall = np.concatenate(([0.0], recall, [1.0]))
    precision = np.concatenate(([0.0], precision, [0.0]))
    precision = np.maximum.accumulate(precision[::-1])[::-1]
    changing = np.flatnonzero(recall[1:] != recall[:-1]) + 1
    return float(np.sum((recall[changing] - recall[changing - 1]) * precision[changing]))


def _classification_metrics(
    true_ids: list[int],
    predicted_ids: list[int],
    *,
    top_ids: list[list[int]] | None = None,
) -> dict[str, object] | None:
    if not true_ids:
        return None
    true = np.asarray(true_ids, dtype=np.int64)
    predicted = np.asarray(predicted_ids, dtype=np.int64)
    classes = np.unique(true)
    precisions: list[float] = []
    recalls: list[float] = []
    f1s: list[float] = []
    for class_id in classes:
        true_positive = int(np.logical_and(true == class_id, predicted == class_id).sum())
        false_positive = int(np.logical_and(true != class_id, predicted == class_id).sum())
        false_negative = int(np.logical_and(true == class_id, predicted != class_id).sum())
        precision = true_positive / (true_positive + false_positive or 1)
        recall = true_positive / (true_positive + false_negative or 1)
        f1 = 2 * precision * recall / (precision + recall or 1)
        precisions.append(precision)
        recalls.append(recall)
        f1s.append(f1)
    result: dict[str, object] = {
        "samples": len(true_ids),
        "classes_with_support": len(classes),
        "accuracy": float((true == predicted).mean()),
        "macro_precision": float(np.mean(precisions)),
        "macro_recall": float(np.mean(recalls)),
        "macro_f1": float(np.mean(f1s)),
    }
    if top_ids is not None:
        top_k_matches = [
            truth in candidates
            for truth, candidates in zip(true_ids, top_ids, strict=True)
        ]
        result["top_k_accuracy"] = float(
            np.mean(top_k_matches)
        )
        result["top_k"] = max((len(values) for values in top_ids), default=0)
    return result


def _attribute_metrics(
    predictions: pd.DataFrame,
    ground_truth: pd.DataFrame,
    matches: list[tuple[int, int, float]],
    *,
    dbh_tolerance_in: float,
) -> dict[str, object]:
    dbh_errors: list[float] = []
    genus_true: list[int] = []
    genus_predicted: list[int] = []
    species_true: list[int] = []
    species_predicted: list[int] = []
    species_top: list[list[int]] = []
    joint_correct = 0
    joint_matched_eligible = 0
    for prediction_index, truth_index, _ in matches:
        prediction = predictions.loc[prediction_index]
        truth = ground_truth.loc[truth_index]
        true_dbh = truth["dbh_log1p"]
        true_genus = truth["genus_id"]
        true_species = truth["species_id"]
        if pd.notna(true_dbh):
            dbh_errors.append(
                float(prediction["dbh_in"]) - float(np.expm1(float(true_dbh)))
            )
        if pd.notna(true_genus):
            genus_true.append(int(true_genus))
            genus_predicted.append(int(prediction["genus_id"]))
        if pd.notna(true_species):
            species_true.append(int(true_species))
            species_predicted.append(int(prediction["species_id"]))
            species_top.append([int(value) for value in prediction["species_top_ids"]])
        if pd.notna(true_dbh) and pd.notna(true_species):
            joint_matched_eligible += 1
            dbh_error = abs(
                float(prediction["dbh_in"]) - float(np.expm1(float(true_dbh)))
            )
            if (
                int(prediction["species_id"]) == int(true_species)
                and dbh_error <= dbh_tolerance_in
            ):
                joint_correct += 1

    all_joint_eligible = int(
        (ground_truth["dbh_log1p"].notna() & ground_truth["species_id"].notna()).sum()
    )
    dbh = None
    if dbh_errors:
        errors = np.asarray(dbh_errors, dtype=np.float64)
        dbh = {
            "samples": len(errors),
            "mae_in": float(np.abs(errors).mean()),
            "rmse_in": float(np.sqrt(np.square(errors).mean())),
            "bias_in": float(errors.mean()),
        }
    return {
        "dbh": dbh,
        "genus": _classification_metrics(genus_true, genus_predicted),
        "species": _classification_metrics(
            species_true, species_predicted, top_ids=species_top
        ),
        "joint": {
            "correct": joint_correct,
            "matched_eligible": joint_matched_eligible,
            "eligible_ground_truth": all_joint_eligible,
            "conditional_accuracy": (
                joint_correct / joint_matched_eligible if joint_matched_eligible else None
            ),
            "recall": joint_correct / all_joint_eligible if all_joint_eligible else None,
        },
    }


def _decode_batch(
    prediction: dict[str, Any],
    chip_ids: list[str],
    *,
    max_detections_per_chip: int,
    nms_kernel: int,
) -> list[dict[str, object]]:
    import torch
    from torch.nn import functional as functional

    center = prediction["center_logits"].sigmoid()
    pooled = functional.max_pool2d(
        center[:, None], kernel_size=nms_kernel, stride=1, padding=nms_kernel // 2
    ).squeeze(1)
    peaks = center.eq(pooled)
    genus_probability = prediction["genus_logits"].softmax(dim=1)
    species_probability = prediction["species_logits"].softmax(dim=1)
    records: list[dict[str, object]] = []
    for batch_index, chip_id in enumerate(chip_ids):
        locations = torch.nonzero(peaks[batch_index], as_tuple=False)
        scores = center[batch_index, locations[:, 0], locations[:, 1]]
        if len(scores) > max_detections_per_chip:
            _, keep = torch.topk(scores, max_detections_per_chip, sorted=True)
            locations = locations[keep]
            scores = scores[keep]
        else:
            order = torch.argsort(scores, descending=True)
            locations = locations[order]
            scores = scores[order]
        top_k = min(5, species_probability.shape[1])
        ys, xs = locations[:, 0], locations[:, 1]
        genus_probs = genus_probability[batch_index, :, ys, xs].transpose(0, 1)
        species_probs = species_probability[batch_index, :, ys, xs].transpose(0, 1)
        genus_confidence, genus_ids = torch.max(genus_probs, dim=1)
        species_values, species_ids = torch.topk(species_probs, top_k, dim=1)
        dbh_values = prediction["dbh_log1p"][batch_index, ys, xs]
        locations_cpu = locations.cpu().numpy()
        scores_cpu = scores.float().cpu().numpy()
        dbh_cpu = dbh_values.float().cpu().numpy()
        genus_ids_cpu = genus_ids.cpu().numpy()
        genus_confidence_cpu = genus_confidence.float().cpu().numpy()
        species_ids_cpu = species_ids.cpu().numpy()
        species_values_cpu = species_values.float().cpu().numpy()
        for index, location in enumerate(locations_cpu):
            y, x = (int(location[0]), int(location[1]))
            dbh_log1p = float(dbh_cpu[index])
            records.append(
                {
                    "chip_id": str(chip_id),
                    "output_x": x,
                    "output_y": y,
                    "score": float(scores_cpu[index]),
                    "dbh_log1p": dbh_log1p,
                    "dbh_in": float(np.expm1(np.clip(dbh_log1p, -10.0, 10.0))),
                    "genus_id": int(genus_ids_cpu[index]),
                    "genus_confidence": float(genus_confidence_cpu[index]),
                    "species_id": int(species_ids_cpu[index, 0]),
                    "species_confidence": float(species_values_cpu[index, 0]),
                    "species_top_ids": [int(value) for value in species_ids_cpu[index]],
                }
            )
    return records


def _add_geography(
    predictions: pd.DataFrame,
    manifest: pd.DataFrame,
    config: ProjectConfig,
    source_raster: Path | None,
) -> pd.DataFrame:
    if predictions.empty:
        return predictions
    offsets = manifest.set_index("chip_id")[["row_offset", "column_offset"]]
    result = predictions.join(offsets, on="chip_id", validate="many_to_one")
    result["pixel_col"] = (
        result["column_offset"] + result["output_x"] * config.targets.output_stride
    )
    result["pixel_row"] = (
        result["row_offset"] + result["output_y"] * config.targets.output_stride
    )
    if source_raster is None or not source_raster.exists():
        return result
    import rasterio
    from pyproj import Transformer

    with rasterio.open(source_raster) as source:
        projected = [
            source.transform * (float(col), float(row))
            for col, row in zip(result["pixel_col"], result["pixel_row"], strict=True)
        ]
        to_wgs84 = Transformer.from_crs(source.crs, "EPSG:4326", always_xy=True)
        longitude, latitude = to_wgs84.transform(
            [value[0] for value in projected], [value[1] for value in projected]
        )
    result["longitude"] = longitude
    result["latitude"] = latitude
    return result


def run_evaluation(
    config: ProjectConfig,
    checkpoint_path: str | Path,
    *,
    split: str = "validation",
    device_name: str = "auto",
    allow_test: bool = False,
    cohort: str | None = None,
) -> dict[str, object]:
    if split not in {"train", "validation", "test"}:
        raise ValueError("split must be train, validation, or test")
    if split == "test" and not allow_test:
        raise ValueError("test evaluation is sealed; pass --allow-test after decisions are frozen")
    output_cohort = cohort or split
    if _COHORT_NAME.fullmatch(output_cohort) is None:
        raise ValueError(
            "evaluation cohort must start with a lowercase letter or digit and contain only "
            "lowercase letters, digits, and hyphens"
        )
    try:
        import torch
        from torch.utils.data import DataLoader
    except ImportError as error:  # pragma: no cover
        raise RuntimeError("Install the train dependency group: uv sync --group train") from error

    from urban_tree_ml.dataset import NpzChipDataset
    from urban_tree_ml.model import RawImageryTreeModel

    checkpoint = Path(checkpoint_path).resolve()
    if not checkpoint.exists():
        raise FileNotFoundError(f"checkpoint does not exist: {checkpoint}")
    chip_root = config.paths.root / "chips" / config.dataset
    manifest_path = chip_root / "chips.parquet"
    labels_path = chip_root / "labels.parquet"
    if not labels_path.exists():
        raise FileNotFoundError("labels.parquet is missing; rebuild chips with the current code")
    selected_taxonomy_path = taxonomy_path(config)
    taxonomy = json.loads(selected_taxonomy_path.read_text(encoding="utf-8"))
    device = (
        torch.device("cuda" if torch.cuda.is_available() else "cpu")
        if device_name == "auto"
        else torch.device(device_name)
    )
    if device.type == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA evaluation requested, but PyTorch cannot access CUDA")
    if device.type == "cuda":
        torch.set_float32_matmul_precision("high")

    network = RawImageryTreeModel(
        input_channels=config.model.input_channels,
        feature_channels=config.model.feature_channels,
        genus_classes=len(taxonomy["genera"]),
        species_classes=len(taxonomy["species"]),
        pretrained=False,
    )
    payload = torch.load(checkpoint, map_location="cpu", weights_only=False)
    state = {
        key.removeprefix("network."): value
        for key, value in payload["state_dict"].items()
        if key.startswith("network.")
    }
    network.load_state_dict(state, strict=True)
    network.to(device).eval()

    dataset = NpzChipDataset(manifest_path, split)
    loader = DataLoader(
        dataset,
        batch_size=config.training.batch_size,
        shuffle=False,
        num_workers=config.training.workers,
        pin_memory=device.type == "cuda",
        persistent_workers=config.training.workers > 0,
    )
    prediction_records: list[dict[str, object]] = []
    with torch.inference_mode():
        for batch in loader:
            image = batch["image"].to(device, non_blocking=True)
            with torch.autocast(
                device_type=device.type,
                dtype=torch.bfloat16,
                enabled=device.type == "cuda",
            ):
                output = network(image)
            prediction_records.extend(
                _decode_batch(
                    output,
                    list(batch["chip_id"]),
                    max_detections_per_chip=config.evaluation.max_detections_per_chip,
                    nms_kernel=config.evaluation.nms_kernel,
                )
            )

    prediction_columns = [
        "chip_id",
        "output_x",
        "output_y",
        "score",
        "dbh_log1p",
        "dbh_in",
        "genus_id",
        "genus_confidence",
        "species_id",
        "species_confidence",
        "species_top_ids",
    ]
    predictions = pd.DataFrame.from_records(prediction_records, columns=prediction_columns)
    manifest = pd.read_parquet(manifest_path)
    summary = json.loads((chip_root / "summary.json").read_text(encoding="utf-8"))
    source_raster_value = summary.get("source_raster")
    source_raster = Path(str(source_raster_value)) if source_raster_value else None
    predictions = _add_geography(predictions, manifest, config, source_raster)
    predictions["above_threshold"] = predictions["score"] >= config.evaluation.confidence_threshold
    predictions["genus"] = predictions["genus_id"].map(
        lambda value: taxonomy["genera"][int(value)]
    )
    predictions["species"] = predictions["species_id"].map(
        lambda value: taxonomy["species"][int(value)]
    )

    ground_truth = pd.read_parquet(labels_path)
    ground_truth = ground_truth[ground_truth["split"] == split].reset_index(drop=True)
    above_threshold = predictions[predictions["above_threshold"]]
    metrics_by_radius: dict[str, object] = {}
    match_records: list[dict[str, object]] = []
    for radius_m in config.evaluation.match_radii_m:
        radius_output_px = radius_m / (
            config.imagery.resolution_m * config.targets.output_stride
        )
        matches = _greedy_matches(
            above_threshold, ground_truth, radius_output_px=radius_output_px
        )
        true_positive = len(matches)
        precision = true_positive / len(above_threshold) if len(above_threshold) else 0.0
        recall = true_positive / len(ground_truth) if len(ground_truth) else 0.0
        for prediction_index, truth_index, distance in matches:
            match_records.append(
                {
                    "radius_m": radius_m,
                    "prediction_index": prediction_index,
                    "tree_id": str(ground_truth.loc[truth_index, "tree_id"]),
                    "distance_m": distance
                    * config.targets.output_stride
                    * config.imagery.resolution_m,
                }
            )
        metrics_by_radius[str(radius_m)] = {
            "detection": {
                "true_positive": true_positive,
                "false_positive": len(above_threshold) - true_positive,
                "false_negative": len(ground_truth) - true_positive,
                "precision": precision,
                "recall": recall,
                "f1": 2 * precision * recall / (precision + recall or 1),
                "average_precision": _average_precision(
                    predictions, ground_truth, radius_output_px=radius_output_px
                ),
            },
            "attributes_on_matched_detections": _attribute_metrics(
                predictions,
                ground_truth,
                matches,
                dbh_tolerance_in=config.evaluation.dbh_tolerance_in,
            ),
        }

    checkpoint_run_dir = (
        checkpoint.parent.parent if checkpoint.parent.name == "checkpoints" else None
    )
    run_dir = checkpoint_run_dir or (config.paths.root / "runs" / config.experiment)
    output_dir = run_dir / "evaluation" / output_cohort
    output_dir.mkdir(parents=True, exist_ok=True)
    predictions_path = output_dir / "predictions.parquet"
    matches_path = output_dir / "matches.parquet"
    ground_truth_path = output_dir / "ground-truth.parquet"
    taxonomy_output_path = output_dir / "taxonomy.json"
    predictions.to_parquet(predictions_path, index=True)
    ground_truth.to_parquet(ground_truth_path, index=False)
    pd.DataFrame.from_records(
        match_records,
        columns=["radius_m", "prediction_index", "tree_id", "distance_m"],
    ).to_parquet(matches_path, index=False)
    taxonomy_output_path.write_text(
        json.dumps(taxonomy, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    result: dict[str, object] = {
        "checkpoint": str(checkpoint),
        "run_id": run_dir.name,
        "cohort": output_cohort,
        "city": config.inventory.city,
        "dataset": config.dataset,
        "split": split,
        "device": str(device),
        "chips": len(dataset),
        "ground_truth_trees": len(ground_truth),
        "candidate_predictions": len(predictions),
        "predictions_above_threshold": len(above_threshold),
        "confidence_threshold": config.evaluation.confidence_threshold,
        "max_detections_per_chip": config.evaluation.max_detections_per_chip,
        "metrics_by_match_radius_m": metrics_by_radius,
        "predictions": str(predictions_path),
        "matches": str(matches_path),
        "ground_truth": str(ground_truth_path),
        "taxonomy": str(taxonomy_output_path),
        "taxonomy_source": str(selected_taxonomy_path),
        "normalization_source": str(chip_root / "normalization.json"),
        "source_raster": str(source_raster) if source_raster is not None else None,
    }
    evaluation_metadata_path = output_dir / "evaluation-metadata.json"
    evaluation_metadata_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "created_at": datetime.now(UTC).isoformat(),
                "run_id": run_dir.name,
                "cohort": output_cohort,
                "split": split,
                "city": config.inventory.city,
                "dataset": config.dataset,
                "checkpoint": str(checkpoint),
                "source_raster": str(source_raster) if source_raster is not None else None,
                "config": config.model_dump(mode="json"),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    result["evaluation_metadata"] = str(evaluation_metadata_path)
    metrics_path = output_dir / "metrics.json"
    metrics_path.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    result["metrics"] = str(metrics_path)
    return result
