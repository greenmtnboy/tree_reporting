from __future__ import annotations

import json
import math
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import numpy as np

from urban_tree_ml.config import ProjectConfig

REVIEW_STATUSES = frozenset({"aligned", "offset", "not-tree", "uncertain", "duplicate"})
_REVIEW_FIELDS = frozenset(
    {"status", "note", "image_x", "image_y", "east_m", "north_m"}
)


def _write_json_atomic(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as output:
            json.dump(value, output, indent=2, sort_keys=True)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_manifest(review_dir: Path) -> dict[str, object]:
    manifest_path = review_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"registration review manifest does not exist at {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest.get("metadata"), dict) or not isinstance(
        manifest.get("samples"), list
    ):
        raise ValueError("registration review manifest has an invalid shape")
    return manifest


def _finite_optional(value: object, field: str) -> float | None:
    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"review field {field!r} must be finite")
    return number


def normalize_review_payload(
    payload: dict[str, object],
    manifest: dict[str, object],
) -> dict[str, dict[str, object]]:
    """Validate browser/server or exported review JSON into one canonical mapping."""
    raw_reviews = payload.get("reviews", {})
    if isinstance(raw_reviews, list):
        review_items = []
        for review in raw_reviews:
            if not isinstance(review, dict) or "sample_id" not in review:
                raise ValueError("every exported review must contain a sample_id")
            review_items.append((str(review["sample_id"]), review))
    elif isinstance(raw_reviews, dict):
        review_items = [(str(sample_id), review) for sample_id, review in raw_reviews.items()]
    else:
        raise ValueError("reviews must be an object or an exported review list")

    samples = {
        str(sample["sample_id"]): sample
        for sample in manifest["samples"]
        if isinstance(sample, dict) and "sample_id" in sample
    }
    normalized: dict[str, dict[str, object]] = {}
    for sample_id, raw_review in review_items:
        if sample_id not in samples:
            raise ValueError(f"review refers to unknown sample {sample_id!r}")
        if not isinstance(raw_review, dict):
            raise ValueError(f"review {sample_id!r} must be an object")
        status = raw_review.get("status")
        if status is not None and status not in REVIEW_STATUSES:
            raise ValueError(f"review {sample_id!r} has unknown status {status!r}")
        review: dict[str, object] = {}
        if status is not None:
            review["status"] = status
        note = raw_review.get("note")
        if note is not None:
            review["note"] = str(note)[:2000]
        for field in _REVIEW_FIELDS - {"status", "note"}:
            number = _finite_optional(raw_review.get(field), field)
            if number is not None:
                review[field] = number
        if review:
            normalized[sample_id] = review
    return normalized


def normalize_scene_review_payload(
    payload: dict[str, object],
    manifest: dict[str, object],
) -> dict[str, dict[str, object]]:
    """Validate persistent scene-level completion state from the review UI."""
    raw_scene_reviews = payload.get("scene_reviews", {})
    if raw_scene_reviews is None:
        raw_scene_reviews = {}
    if not isinstance(raw_scene_reviews, dict):
        raise ValueError("scene_reviews must be an object")

    scenes = manifest.get("scenes", [])
    if not isinstance(scenes, list):
        raise ValueError("registration review manifest scenes must be a list")
    scene_ids = {
        str(scene["scene_id"])
        for scene in scenes
        if isinstance(scene, dict) and "scene_id" in scene
    }
    normalized: dict[str, dict[str, object]] = {}
    for raw_scene_id, raw_review in raw_scene_reviews.items():
        scene_id = str(raw_scene_id)
        if scene_id not in scene_ids:
            raise ValueError(f"scene review refers to unknown scene {scene_id!r}")
        if not isinstance(raw_review, dict):
            raise ValueError(f"scene review {scene_id!r} must be an object")
        done = raw_review.get("done")
        if done is None or done is False:
            continue
        if done is not True:
            raise ValueError(f"scene review {scene_id!r} done must be a boolean")
        review: dict[str, object] = {"done": True}
        completed_at = raw_review.get("completed_at")
        if completed_at is not None:
            review["completed_at"] = str(completed_at)[:100]
        normalized[scene_id] = review
    return normalized


def persist_review_payload(review_dir: str | Path, payload: dict[str, object]) -> dict[str, object]:
    directory = Path(review_dir)
    manifest = _read_manifest(directory)
    metadata = manifest["metadata"]
    payload_metadata = payload.get("metadata", {})
    if isinstance(payload_metadata, dict) and payload_metadata.get("review_id") not in (
        None,
        metadata.get("review_id"),
    ):
        raise ValueError("review payload does not match this registration review")
    reviews = normalize_review_payload(payload, manifest)
    path = directory / "reviews.json"
    if "scene_reviews" in payload:
        scene_reviews = normalize_scene_review_payload(payload, manifest)
    elif path.exists():
        scene_reviews = load_persisted_reviews(directory)["scene_reviews"]
    else:
        scene_reviews = {}
    persisted = {
        "schema_version": 1,
        "metadata": metadata,
        "saved_at": datetime.now(UTC).isoformat(),
        "reviews": reviews,
        "scene_reviews": scene_reviews,
    }
    _write_json_atomic(path, persisted)
    return {
        "path": str(path),
        "reviews": len(reviews),
        "completed_scenes": len(scene_reviews),
    }


def load_persisted_reviews(review_dir: str | Path) -> dict[str, object]:
    directory = Path(review_dir)
    path = directory / "reviews.json"
    if not path.exists():
        return {"schema_version": 1, "reviews": {}, "scene_reviews": {}}
    manifest = _read_manifest(directory)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload_metadata = payload.get("metadata", {})
    if isinstance(payload_metadata, dict) and payload_metadata.get("review_id") not in (
        None,
        manifest["metadata"].get("review_id"),
    ):
        return {
            "schema_version": 1,
            "metadata": manifest["metadata"],
            "reviews": {},
            "scene_reviews": {},
        }
    return {
        "schema_version": 1,
        "metadata": manifest["metadata"],
        "reviews": normalize_review_payload(payload, manifest),
        "scene_reviews": normalize_scene_review_payload(payload, manifest),
    }


def _offset_for_review(review: dict[str, object]) -> tuple[float, float]:
    status = review.get("status")
    if status != "offset":
        return 0.0, 0.0
    east = review.get("east_m")
    north = review.get("north_m")
    if east is None or north is None:
        raise ValueError("reviews marked offset must include a clicked location")
    return float(east), float(north)


def _offset_summary(offsets: list[tuple[float, float]]) -> dict[str, float | int | None]:
    if not offsets:
        return {"samples": 0, "east_median": None, "north_median": None, "radial_mad": None}
    array = np.asarray(offsets, dtype=np.float64)
    median = np.median(array, axis=0)
    radial_deviation = np.sqrt(np.square(array - median).sum(axis=1))
    return {
        "samples": len(offsets),
        "east_median": float(median[0]),
        "north_median": float(median[1]),
        "radial_mad": float(np.median(radial_deviation)),
    }


def finalize_registration_feedback(
    config: ProjectConfig,
    raster_path: str | Path,
    *,
    review_dir: str | Path | None = None,
    reviews_path: str | Path | None = None,
    minimum_training_reviews: int = 20,
) -> dict[str, object]:
    if minimum_training_reviews < 1:
        raise ValueError("minimum_training_reviews must be at least one")
    raster = Path(raster_path).resolve()
    directory = (
        Path(review_dir)
        if review_dir is not None
        else config.paths.root / "qa" / "registration" / raster.stem
    )
    manifest = _read_manifest(directory)
    metadata = manifest["metadata"]
    manifest_raster = Path(str(metadata.get("source_raster", "")))
    if manifest_raster.name != raster.name:
        raise ValueError("registration review was generated for a different raster")

    source_reviews = Path(reviews_path) if reviews_path is not None else directory / "reviews.json"
    if not source_reviews.exists():
        raise FileNotFoundError(
            f"no saved reviews found at {source_reviews}; use the served UI or export its JSON"
        )
    raw_payload = json.loads(source_reviews.read_text(encoding="utf-8"))
    payload_metadata = raw_payload.get("metadata", {})
    if isinstance(payload_metadata, dict) and payload_metadata.get("review_id") not in (
        None,
        metadata.get("review_id"),
    ):
        raise ValueError("saved reviews do not match this registration review")
    reviews = normalize_review_payload(raw_payload, manifest)
    scene_reviews = normalize_scene_review_payload(raw_payload, manifest)
    canonical = {
        "schema_version": 1,
        "metadata": metadata,
        "saved_at": datetime.now(UTC).isoformat(),
        "reviews": reviews,
        "scene_reviews": scene_reviews,
    }
    _write_json_atomic(directory / "reviews.json", canonical)

    samples = {str(sample["sample_id"]): sample for sample in manifest["samples"]}
    status_counts = {status: 0 for status in sorted(REVIEW_STATUSES)}
    training_offsets: list[tuple[float, float]] = []
    validation_offsets: list[tuple[float, float]] = []
    exclusions: list[dict[str, str]] = []
    point_corrections: list[dict[str, str | float]] = []
    ignored_test_reviews = 0
    for sample_id, review in reviews.items():
        status = review.get("status")
        if status is None:
            continue
        status_counts[str(status)] += 1
        sample = samples[sample_id]
        split = str(sample["split"])
        if split == "test":
            ignored_test_reviews += 1
            continue
        if status in {"not-tree", "uncertain", "duplicate"}:
            exclusions.append(
                {
                    "tree_id": str(sample["tree_id"]),
                    "split": split,
                    "reason": str(status),
                    "sample_id": sample_id,
                }
            )
        else:
            offset = _offset_for_review(review)
            if status == "offset":
                point_corrections.append(
                    {
                        "tree_id": str(sample["tree_id"]),
                        "split": split,
                        "sample_id": sample_id,
                        "east_m": offset[0],
                        "north_m": offset[1],
                    }
                )
            if split == "train":
                training_offsets.append(offset)
            elif split == "validation":
                validation_offsets.append(offset)

    training_summary = _offset_summary(training_offsets)
    enough_reviews = len(training_offsets) >= minimum_training_reviews
    east_m = float(training_summary["east_median"] or 0.0) if enough_reviews else 0.0
    north_m = float(training_summary["north_median"] or 0.0) if enough_reviews else 0.0
    validation_residuals = [
        (east - east_m, north - north_m) for east, north in validation_offsets
    ]
    feedback = {
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "dataset": config.dataset,
        "review_id": metadata.get("review_id"),
        "source_raster_name": raster.name,
        "source_raster": str(raster),
        "reviews": {
            "status_counts": status_counts,
            "ignored_test_reviews": ignored_test_reviews,
            "completed_scenes": len(scene_reviews),
        },
        "registration": {
            "status": "applied" if enough_reviews else "insufficient-training-reviews",
            "minimum_training_reviews": minimum_training_reviews,
            "east_m": east_m,
            "north_m": north_m,
            "training": training_summary,
            "validation_residual": _offset_summary(validation_residuals),
        },
        "exclusions": exclusions,
        "point_corrections": point_corrections,
    }
    feedback_path = directory / "training-feedback.json"
    _write_json_atomic(feedback_path, feedback)
    return {
        "feedback": str(feedback_path),
        "registration_status": feedback["registration"]["status"],
        "correction_m": {"east": east_m, "north": north_m},
        "training_registration_reviews": len(training_offsets),
        "validation_registration_reviews": len(validation_offsets),
        "excluded_points": len(exclusions),
        "point_corrected_points": len(point_corrections),
        "ignored_test_reviews": ignored_test_reviews,
        "completed_scenes": len(scene_reviews),
    }


def load_training_feedback(
    path: str | Path,
    raster_path: str | Path,
    dataset: str,
) -> dict[str, object]:
    feedback_path = Path(path)
    feedback = json.loads(feedback_path.read_text(encoding="utf-8"))
    if feedback.get("schema_version") != 1:
        raise ValueError("unsupported registration feedback schema")
    if feedback.get("dataset") != dataset:
        raise ValueError("registration feedback was finalized for a different dataset")
    if feedback.get("source_raster_name") != Path(raster_path).name:
        raise ValueError("registration feedback was finalized for a different raster")
    registration = feedback.get("registration")
    if not isinstance(registration, dict):
        raise ValueError("registration feedback is missing its correction")
    exclusions = feedback.get("exclusions")
    if not isinstance(exclusions, list):
        raise ValueError("registration feedback exclusions must be a list")
    point_corrections = feedback.get("point_corrections", [])
    if not isinstance(point_corrections, list):
        raise ValueError("registration feedback point corrections must be a list")
    correction_keys: set[tuple[str, str]] = set()
    for correction in point_corrections:
        if not isinstance(correction, dict):
            raise ValueError("every registration point correction must be an object")
        required = {"tree_id", "split", "east_m", "north_m"}
        if not required.issubset(correction):
            raise ValueError("registration point correction is missing required fields")
        split = str(correction["split"])
        if split not in {"train", "validation"}:
            raise ValueError("registration point corrections may only target development splits")
        _finite_optional(correction["east_m"], "east_m")
        _finite_optional(correction["north_m"], "north_m")
        key = (str(correction["tree_id"]), split)
        if key in correction_keys:
            raise ValueError(f"duplicate registration point correction for {key!r}")
        correction_keys.add(key)
    return feedback
