# The embedded, dependency-free browser UI is clearer when its HTML/JavaScript
# is not wrapped to Python's line-length limit.
# ruff: noqa: E501

from __future__ import annotations

import io
import json
import os
import re
import warnings
import weakref
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock

import numpy as np
import polars as pl
from urban_tree_ml.review_tables import above_threshold, read_table, review_backfill_directory, scan_predictions
from pydantic import ValidationError

from urban_tree_ml.config import ProjectConfig
from urban_tree_ml.chip_geometry import CHIP_ID as _CHIP_ID, chip_offsets, corresponding_chip_id
from urban_tree_ml.model_cache import ModelBundleCache, retained_bytes
from urban_tree_ml.quality import _rgb_preview
from urban_tree_ml.review_assignments import review_assignments
from urban_tree_ml.targets import PointLabel, build_targets



def _describe_center_supervision(
    mask: float,
    target: float,
    *,
    exact: bool,
) -> dict[str, object]:
    if mask < 0.5:
        status = "ignored"
        label = "Ignored by center loss"
        negative_weight = 0.0
        would_penalize = False
    elif target >= 1 - 1e-6:
        status = "positive"
        label = "Positive tree center"
        negative_weight = 0.0
        would_penalize = False
    elif target > 0:
        status = "near-positive"
        negative_weight = float((1 - target) ** 4)
        label = "Downweighted negative near a tree center"
        would_penalize = True
    else:
        status = "negative"
        label = "Penalized as trusted background"
        negative_weight = 1.0
        would_penalize = True
    return {
        "status": status,
        "label": label,
        "would_penalize": would_penalize,
        "negative_weight": negative_weight,
        "exact": exact,
    }


def _read_predictions(path):
    frame = scan_predictions(path)
    schema = frame.collect_schema()
    repeated = ('chip_id', 'genus', 'species', 'vegetation_type', 'class_name')
    return frame.with_columns(pl.col(c).cast(pl.Categorical) for c in repeated
                              if schema.get(c) == pl.String).collect()


def _index_chips(frame):
    # Most exports already group chips. A stable sort handles interleaved exports
    # without changing score ties or the original order of trees within a chip.
    lengths = frame['chip_id'].rle()
    if lengths.struct.field('value').n_unique() != len(lengths):
        frame = frame.sort('chip_id', maintain_order=True)
        lengths = frame['chip_id'].rle()
    offset = 0
    slices = {}
    for size, chip in lengths.struct.unnest().select('len', 'value').iter_rows():
        slices[chip] = (offset, size)
        offset += size
    return frame, slices


def _training_curves(run_dir: Path) -> dict[str, list[dict[str, float | int]]]:
    event_paths = sorted(
        run_dir.glob("lightning_logs/**/events.out.tfevents.*"),
        key=lambda path: path.stat().st_mtime,
    )
    if not event_paths:
        return {}
    try:
        from tensorboard.backend.event_processing.event_accumulator import EventAccumulator
    except ImportError:  # pragma: no cover - train installs include tensorboard
        return {}
    accumulator = EventAccumulator(str(event_paths[-1])).Reload()
    wanted = {
        "train/loss_epoch",
        "validation/loss",
        "train/center_loss_epoch",
        "validation/center_loss",
        "train/dbh_loss_epoch",
        "validation/dbh_loss",
        "train/species_loss_epoch",
        "validation/species_loss",
    }
    return {
        tag: [
            {"step": int(item.step), "value": float(item.value)}
            for item in accumulator.Scalars(tag)
        ]
        for tag in accumulator.Tags().get("scalars", [])
        if tag in wanted
    }


def comparison_order(current, choices):
    """Prefer the closest earlier version of this model series and matching cohort."""
    def series(run):
        match = re.search(r"(?:^|-)v(\d+)(?:-|$)", run)
        return (run[:match.start()], int(match[1])) if match else (run, 0)

    prefix, version = series(current['training_run_id'])
    def priority(row):
        family = row['training_run_id']
        name, number = series(family)
        parent = family == current.get('parent_run_id')
        related = prefix == name or parent
        prior = number < version and number > 0
        return (related and prior, number if related and prior else 0, parent,
                prior, number if prior else 0, row['cohort'] == current['cohort'],
                row['created_at'], row['run_id'])

    candidates = [r for r in choices if r['training_run_id'] != current['training_run_id']
                  and (series(r['training_run_id'])[1] < version or
                       r['training_run_id'] == current.get('parent_run_id') or
                       (not version and r['created_at'] < current['created_at']))]
    return sorted(candidates, key=priority, reverse=True)


class ModelDebugBundle:
    """Read-only view over one saved model evaluation for the local QA server."""

    def __init__(
        self,
        config: ProjectConfig,
        evaluation_dir: str | Path,
        raster_path: str | Path,
        *,
        evaluation_id: str | None = None,
        artifact_root: str | Path | None = None,
    ) -> None:
        self.config = config
        self.directory = Path(evaluation_dir).resolve()
        self.raster_path = Path(raster_path).resolve()
        self.artifact_root = Path(artifact_root or config.paths.root).resolve()
        required = {
            "metrics": self.directory / "metrics.json",
            "predictions": self.directory / "predictions.parquet",
            "ground truth": self.directory / "ground-truth.parquet",
            "matches": self.directory / "matches.parquet",
            "taxonomy": self.directory / "taxonomy.json",
        }
        missing = [name for name, path in required.items() if not path.exists()]
        if missing:
            raise FileNotFoundError(
                f"evaluation bundle is missing {', '.join(missing)} under {self.directory}"
            )
        self.metrics = json.loads(required["metrics"].read_text(encoding="utf-8"))
        self.taxonomy = json.loads(required["taxonomy"].read_text(encoding="utf-8"))
        self.predictions = _read_predictions(required["predictions"])
        from urban_tree_ml.peer_references import reference
        peer = reference(self.directory)
        self.provisional_reference = peer[1] if peer else None
        self.ground_truth = read_table(peer[0] if peer else required["ground truth"])
        self.matches = read_table(required["matches"])
        self.run_dir = self.directory.parent.parent
        self.training_run_id = self.run_dir.name
        self.cohort = str(self.metrics.get("cohort") or self.directory.name)
        self.evaluation_id = evaluation_id or (
            self.training_run_id
            if self.cohort == "validation"
            else f"{self.training_run_id}::{self.cohort}"
        )
        metadata_path = self.run_dir / "run-metadata.json"
        self.run_metadata = (
            json.loads(metadata_path.read_text(encoding="utf-8"))
            if metadata_path.exists()
            else None
        )
        self.chip_ids = sorted(
            set(self.predictions["chip_id"].unique())
            | set(self.ground_truth["chip_id"].unique())
        )
        coverage_path = self.directory / 'inference-coverage.json'
        if coverage_path.exists():
            coverage = json.loads(coverage_path.read_text(encoding='utf-8'))
            if coverage.get('complete'):
                self.chip_ids = sorted(set(self.chip_ids) | {str(row['chip_id']) for row in coverage['chips']})
        self._image_cache: dict[str, bytes] = {}
        self._target_cache: dict[str, tuple[np.ndarray, np.ndarray, bool]] = {}
        self._collision_locations: pl.DataFrame | None = None
        self._add_ground_truth_names()
        # Index once instead of scanning millions of predictions for every card.
        self.predictions, self._prediction_rows = _index_chips(self.predictions)
        self.ground_truth, self._truth_rows = _index_chips(self.ground_truth)
        self.chips = self._chip_summaries()

    def truth_for_chip(self, chip):
        return self.ground_truth.slice(*self._truth_rows.get(chip, (0, 0)))

    def _add_ground_truth_names(self) -> None:
        truth = self.ground_truth
        for name, vocabulary in [('genus', self.taxonomy['genera']), ('species', self.taxonomy['species'])]:
            ids = pl.col(name + '_id').cast(pl.Int64, strict=False)
            truth = truth.with_columns(ids.replace_strict(dict(enumerate(vocabulary)),
                                        default=None, return_dtype=pl.String).alias(name))
        if 'inventory_species' not in truth:
            truth = truth.with_columns(pl.lit(None, dtype=pl.String).alias('inventory_species'))
        inventory_path = (self.artifact_root / 'run-inputs' / self.training_run_id /
                          self.config.inventory.city.lower() / 'inventory.parquet')
        if inventory_path.exists():
            inventory = (pl.scan_parquet(inventory_path).select('tree_id', pl.col('species').alias('_name'))
                         .unique('tree_id', keep='first').collect())
            if inventory.height and truth.height:
                truth = (truth.join(inventory, on='tree_id', how='left', maintain_order='left')
                         .with_columns(pl.coalesce('inventory_species', '_name').alias('inventory_species'))
                         .drop('_name'))
        self.ground_truth = truth.with_columns(np.expm1(pl.col('dbh_log1p').cast(pl.Float64)).alias('dbh_in'))

    def _chip_summaries(self, radius_m: float | None = None) -> list[dict[str, object]]:
        threshold = float(self.metrics["confidence_threshold"])
        if getattr(self, 'provisional_reference', None):
            from urban_tree_ml.review_ranking import calculate
            return calculate(self,threshold,radius_m or 2.0)
        if self.metrics.get('prediction_only'):
            counts = dict(self.predictions.filter(above_threshold(threshold)).group_by('chip_id').len().iter_rows())
            return [dict(chip_id=chip_id, predictions=int(counts.get(chip_id, 0)),
                         prediction_only=True, ground_truth=None, matched=None,
                         missed=None, false_positive=None, goal_f2=None,
                         goal_false_positive=None, species_errors=None,
                         detection_f1=None, error_score=None) for chip_id in self.chip_ids]
        radii = sorted(float(value) for value in self.matches["radius_m"].unique())
        primary_radius = radius_m if radius_m is not None else (radii[0] if radii else None)
        primary = (self.matches.filter(pl.col('radius_m') == primary_radius)
                   if primary_radius is not None else self.matches.head(0))
        # Join the exported prediction index, never its position after sorting.
        links = primary.select(pl.col('prediction_index').cast(pl.Int64).alias('prediction_id'), 'tree_id')
        pred_keys = self.predictions.select('prediction_id', 'chip_id', 'species_id')
        truth_keys = self.ground_truth.select(pl.col('chip_id').cast(pl.String), 'tree_id', 'species_id')
        links = links.join(pred_keys, on='prediction_id', validate='m:1').with_columns(pl.col('chip_id').cast(pl.String))
        tree_keys = ['chip_id', 'tree_id']
        if links.height:
            links = links.join(truth_keys, on=tree_keys, suffix='_truth', validate='m:1')
        if links.height != primary.height:
            raise ValueError('Saved matches reference missing predictions or trees')
        def counts(frame):
            return dict(frame.group_by('chip_id').len().iter_rows())

        species_errors = counts(links.filter(
            (pl.col('species_id') != pl.col('species_id_truth')) &
            pl.col('species_id').is_finite() & pl.col('species_id_truth').is_finite()
        )) if links.height else {}
        matched_truth_counts = counts(links.unique(tree_keys))
        truth_counts = counts(truth_keys)
        has_masks = 'detection_mask_value' in self.predictions
        columns = ['prediction_id', 'chip_id', 'score'] + (['detection_mask_value'] if has_masks else [])
        selected = self.predictions.select(columns).filter(above_threshold(threshold))
        # Compute membership once across the table. Inside a grouped aggregation
        # Polars would build a copy of the full ID lookup for every chip.
        selected = selected.with_columns(pl.col('prediction_id').is_in(links['prediction_id'].implode()).alias('_matched'))
        matched = pl.col('_matched')
        metrics = [pl.len().alias('predicted'), matched.sum().alias('matched')]
        if has_masks:
            mask = pl.col('detection_mask_value')
            metrics.extend([mask.is_finite().fill_null(False).all().alias('valid'),
                            ((mask <= 0) & ~matched).sum().alias('ignored')])
        totals = {row['chip_id']: row for row in selected.group_by('chip_id').agg(metrics).iter_rows(named=True)}
        from urban_tree_ml.goal_metrics import detection
        summaries: list[dict[str, object]] = []
        for chip_id in self.chip_ids:
            matched_predictions = totals.get(chip_id, {}).get('matched', 0)
            missed = truth_counts.get(chip_id, 0) - matched_truth_counts.get(chip_id, 0)
            false_positive = totals.get(chip_id, {}).get('predicted', 0) - matched_predictions
            ignored = totals.get(chip_id, {}).get('ignored', 0)
            goal = detection(matched_predictions, false_positive-ignored, missed, ignored) if has_masks and totals.get(chip_id, {}).get('valid', True) else None
            detection_denominator = 2 * matched_predictions + false_positive + missed
            detection_f1 = (
                2 * matched_predictions / detection_denominator
                if detection_denominator
                else 1.0
            )
            chip_species_errors = species_errors.get(chip_id, 0)
            summaries.append(
                {
                    "chip_id": chip_id,
                    "ground_truth": truth_counts.get(chip_id, 0),
                    "predictions": totals.get(chip_id, {}).get('predicted', 0),
                    "matched": matched_predictions,
                    "missed": missed,
                    "false_positive": false_positive,
                    "goal_f2": goal['f2'] if goal else None,
                    "goal_false_positive": goal['false_positive'] if goal else None,
                    "species_errors": chip_species_errors,
                    "detection_f1": detection_f1,
                    "error_score": missed + false_positive + chip_species_errors,
                }
            )
        return summaries

    def summary(self) -> dict[str, object]:
        from urban_tree_ml.goal_metrics import goal_metrics
        return {
            "run_id": self.evaluation_id,
            "training_run_id": self.training_run_id,
            "cohort": self.cohort,
            "city": self.config.inventory.city,
            "dataset": self.config.dataset,
            "metrics": self.metrics,
            "goal_metrics": goal_metrics(self.directory),
            "training_curves": _training_curves(self.run_dir),
            "run_metadata": self.run_metadata,
            "chips": self.chips,
            "assignments": review_assignments(
                self.artifact_root, self.training_run_id, self.config.inventory.city
            ),
            "display": {
                "chip_pixels": self.config.imagery.chip_pixels,
                "output_stride": self.config.targets.output_stride,
                "resolution_m": self.config.imagery.resolution_m,
            },
        }

    def chip(self, chip_id: str) -> dict[str, object]:
        if chip_id not in self.chip_ids:
            raise KeyError(f"unknown evaluation chip {chip_id!r}")
        predictions = self.predictions.slice(*self._prediction_rows.get(chip_id, (0, 0)))
        predictions = self._add_center_supervision(chip_id, predictions)
        from urban_tree_ml.chip_tags import disagreement, expected_genus
        predictions = predictions.with_columns(
            pl.Series('genus_species_disagreement', [disagreement(self.taxonomy, sid, gid)
                      for sid, gid in predictions.select('species_id', 'genus_id').iter_rows()], dtype=pl.Boolean),
            pl.Series('species_expected_genus', [expected_genus(self.taxonomy, sid)
                      for sid in predictions['species_id']], dtype=pl.String))
        truth = self.truth_for_chip(chip_id)
        return {
            "chip_id": chip_id,
            "prediction_only": bool(self.metrics.get('prediction_only')),
            "provisional_reference": self.provisional_reference,
            "predictions": json.loads(predictions.write_json()),
            "ground_truth": json.loads(truth.write_json()),
            "confidence_threshold": float(self.metrics["confidence_threshold"]),
            "display": {
                "chip_pixels": self.config.imagery.chip_pixels,
                "output_stride": self.config.targets.output_stride,
                "resolution_m": self.config.imagery.resolution_m,
            },
        }

    def _add_center_supervision(
        self,
        chip_id: str,
        predictions: pl.DataFrame,
    ) -> pl.DataFrame:
        exact = True
        if not {"center_target", "detection_mask_value"}.issubset(predictions.columns):
            center, mask, exact = self._chip_targets(chip_id)
            x, y = (predictions[c].cast(pl.Int64).to_numpy() for c in ('output_x', 'output_y'))
            predictions = predictions.with_columns(pl.Series('center_target', center[y, x]),
                                                    pl.Series('detection_mask_value', mask[y, x]))
        supervision = [_describe_center_supervision(float(mask) if mask is not None else np.nan,
                        float(target) if target is not None else np.nan, exact=exact)
                       for mask, target in predictions.select('detection_mask_value', 'center_target').iter_rows()]
        return predictions.with_columns(pl.Series('center_supervision', supervision))

    def _chip_targets(self, chip_id: str) -> tuple[np.ndarray, np.ndarray, bool]:
        cached = self._target_cache.get(chip_id)
        if cached is not None:
            return cached
        split = str(self.metrics.get("split") or "validation")
        chip_root = self.artifact_root / "chips" / self.config.dataset
        chip_path = chip_root / split / f"{chip_id}.npz"
        if chip_path.exists():
            with np.load(chip_path, allow_pickle=False) as chip:
                targets = (
                    chip["center"].copy(),
                    chip["detection_mask"].copy(),
                    True,
                )
            self._cache_targets(chip_id, targets)
            return targets

        match = _CHIP_ID.fullmatch(chip_id)
        if match is None:
            raise KeyError(f"unknown evaluation chip {chip_id!r}")
        try:
            import rasterio
            from rasterio.windows import Window
        except ImportError as error:  # pragma: no cover
            raise RuntimeError("Install the imagery dependency group") from error
        chip_pixels = self.config.imagery.chip_pixels
        stride = self.config.targets.output_stride
        row_offset, column_offset = chip_offsets(chip_id, chip_pixels)
        with rasterio.open(self.raster_path) as source:
            window = Window(column_offset, row_offset, chip_pixels, chip_pixels)
            raw = source.read(self.config.imagery.bands, window=window)
            masks = source.read_masks(self.config.imagery.bands, window=window)
        image = raw.astype(np.float32) * self.config.imagery.input_scale
        denominator = image[3] + image[0] if image.shape[0] >= 4 else None
        ndvi = (
            np.divide(
                image[3] - image[0],
                denominator,
                out=np.zeros_like(image[0]),
                where=np.abs(denominator) > 1e-6,
            )
            if denominator is not None
            else None
        )
        labels = [PointLabel(x=float(x) * stride, y=float(y) * stride)
                  for x, y in self.truth_for_chip(chip_id).select('output_x', 'output_y').iter_rows()]
        ignored_locations, exact = self._ignored_collision_locations(chip_root, chip_id)
        targets = build_targets(
            chip_pixels,
            chip_pixels,
            labels,
            stride=stride,
            gaussian_sigma_px=self.config.targets.gaussian_sigma_px,
            supervision_radius_px=(
                self.config.targets.positive_supervision_radius_m
                / self.config.imagery.resolution_m
            ),
            valid_mask=np.all(masks > 0, axis=0),
            ndvi=ndvi,
            ignored_locations=ignored_locations,
            background_mode=self.config.targets.background_mode,
            background_ndvi_max=self.config.targets.background_ndvi_max,
            collision_policy=self.config.targets.collision_policy,
        )
        output = (targets["center"], targets["detection_mask"], exact)
        self._cache_targets(chip_id, output)
        return output

    def _ignored_collision_locations(
        self,
        chip_root: Path,
        chip_id: str,
    ) -> tuple[list[tuple[float, float]], bool]:
        summary_path = chip_root / "summary.json"
        summary = (
            json.loads(summary_path.read_text(encoding="utf-8"))
            if summary_path.exists()
            else {}
        )
        collision_path = chip_root / "collision-exclusions.parquet"
        if self._collision_locations is None:
            self._collision_locations = (
                read_table(collision_path)
                if collision_path.exists()
                else pl.DataFrame(schema={'chip_id': pl.String, 'output_x': pl.Float64, 'output_y': pl.Float64})
            )
            self._cache_changed('_collision_locations')
        collisions = self._collision_locations.filter(pl.col('chip_id') == chip_id)
        stride = self.config.targets.output_stride
        ignored = [(float(x)*stride, float(y)*stride)
                   for x,y in collisions.select('output_x', 'output_y').iter_rows()]
        collisions_complete = bool(collision_path.exists()) or not int(
            summary.get("collision_excluded_points", 0)
        )
        feedback_complete = not int(summary.get("feedback_excluded_points", 0))
        return ignored, collisions_complete and feedback_complete

    def _cache_targets(
        self,
        chip_id: str,
        targets: tuple[np.ndarray, np.ndarray, bool],
    ) -> None:
        if len(self._target_cache) >= 128:
            self._target_cache.pop(next(iter(self._target_cache)))
        self._target_cache[chip_id] = targets
        self._cache_changed('_target_cache')

    def _cache_changed(self, component):
        callback = getattr(self, '_cache_account', None)
        if callback:
            callback(self, component, retained_bytes(getattr(self, component)))

    def chip_image(self, chip_id: str) -> bytes:
        cached = self._image_cache.get(chip_id)
        if cached is not None:
            return cached
        match = _CHIP_ID.fullmatch(chip_id)
        if match is None or chip_id not in self.chip_ids:
            raise KeyError(f"unknown evaluation chip {chip_id!r}")
        try:
            import rasterio
            from PIL import Image
            from rasterio.windows import Window
        except ImportError as error:  # pragma: no cover
            raise RuntimeError("Install the imagery dependency group") from error
        row_offset, column_offset = chip_offsets(chip_id, self.config.imagery.chip_pixels)
        window = Window(
            column_offset,
            row_offset,
            self.config.imagery.chip_pixels,
            self.config.imagery.chip_pixels,
        )
        with rasterio.open(self.raster_path) as source:
            raw = source.read(self.config.imagery.bands[:3], window=window)
        preview = Image.fromarray(_rgb_preview(raw, self.config.imagery.input_scale))
        output = io.BytesIO()
        preview.save(output, format="PNG", optimize=True)
        encoded = output.getvalue()
        if len(self._image_cache) >= 128:
            self._image_cache.pop(next(iter(self._image_cache)))
        self._image_cache[chip_id] = encoded
        self._cache_changed('_image_cache')
        return encoded


class RunDebugCatalog:
    """Discover internal and external evaluations and lazily open their bundles."""

    def __init__(
        self,
        config: ProjectConfig,
        selected_evaluation_dir: str | Path,
        raster_path: str | Path,
    ) -> None:
        self.config = config
        self.selected_evaluation_dir = Path(selected_evaluation_dir).resolve()
        self.raster_path = Path(raster_path).resolve()
        self.runs_root = self.selected_evaluation_dir.parents[2]
        self._bundles = ModelBundleCache(int(os.environ.get('TREE_ML_MODEL_CACHE_MB', '1024')) * 1024 * 1024)
        self._records_lock = RLock()
        self._summary_lock = RLock()
        self._records_version = self._catalog_version()
        self._summary_version = None
        self._summary_cache = None
        self._runs = self._discover_runs()
        selected_training_run = self.selected_evaluation_dir.parent.parent.name
        selected_cohort = self.selected_evaluation_dir.name
        selected_run_id = (
            selected_training_run
            if selected_cohort == "validation"
            else f"{selected_training_run}::{selected_cohort}"
        )
        self.selected_run_id = (
            selected_run_id
            if selected_run_id in self._runs
            else (next(reversed(self._runs)) if self._runs else None)
        )

    @staticmethod
    def _file_versions(paths):
        """Stat only: never read prediction pixels/rows on navigation requests."""
        result = []
        for path in sorted(set(paths)):
            try:
                stat = path.stat()
                version = (stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size)
            except FileNotFoundError:
                version = None
            result.append((str(path), version))
        return tuple(result)

    def _catalog_version(self):
        paths = []
        for metrics in self.runs_root.glob('*/evaluation/*/metrics.json'):
            paths.extend((metrics, metrics.parent / 'evaluation-metadata.json',
                          metrics.parents[2] / 'run-metadata.json'))
        # A restored local VRT must also refresh raster resolution, even when
        # the downloaded evaluation metadata itself has not changed.
        imagery = self.config.paths.root / 'imagery'
        paths.append(imagery)
        paths.extend(imagery.glob('*'))
        paths.extend(imagery.glob('*/*'))
        return self._file_versions(paths)

    def records(self):
        """Read-only metadata snapshot, refreshed on additions/changes/removals.

        Keep the metadata lock separate from expensive history scoring so a
        cold history request cannot hold up another tab's navigation.
        """
        with self._records_lock:
            version = self._catalog_version()
            if version != self._records_version:
                self._runs = self._discover_runs()
                self._records_version = version
            return list(self._runs.values())

    def _discover_runs(self) -> dict[str, dict[str, object]]:
        records: list[tuple[str, dict[str, object]]] = []
        for metrics_path in self.runs_root.glob("*/evaluation/*/metrics.json"):
            evaluation_dir = metrics_path.parent.resolve()
            # Paired-parent evaluations contain the ancestor's weights, not
            # this run's selected model. Keep these immutable audit artifacts
            # out of review/history/queue discovery: their validation chip IDs
            # intentionally duplicate the selected model's validation cohorts.
            if evaluation_dir.name == 'parent-validation' or evaluation_dir.name.startswith('parent-validation-'):
                continue
            run_dir = evaluation_dir.parent.parent
            metadata_path = run_dir / "run-metadata.json"
            evaluation_metadata_path = evaluation_dir / "evaluation-metadata.json"
            try:
                metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
                metadata = (
                    json.loads(metadata_path.read_text(encoding="utf-8"))
                    if metadata_path.exists()
                    else None
                )
                evaluation_metadata = (
                    json.loads(evaluation_metadata_path.read_text(encoding="utf-8"))
                    if evaluation_metadata_path.exists()
                    else None
                )
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            config_data = (
                evaluation_metadata.get("config", {})
                if isinstance(evaluation_metadata, dict)
                else metadata.get("config", {})
                if isinstance(metadata, dict)
                else {}
            )
            if not config_data:
                # Legacy local evaluations did not save a config. Do not apply
                # this fallback to explicitly identified external-city results.
                if metrics.get("city", self.config.inventory.city) != self.config.inventory.city:
                    warnings.warn(f"Skipping external evaluation without config: {evaluation_dir}", stacklevel=2)
                    continue
                config_data = self.config.model_dump(mode="json")
            try:
                bundle_config = ProjectConfig.model_validate(config_data)
            except ValidationError as error:
                # New training options must not relabel external-city evaluations
                # with the review server's default city. Ignore only unknown keys;
                # malformed known settings remain invalid, rather than guessed.
                compatible_data = json.loads(json.dumps(config_data))
                for issue in error.errors():
                    if issue["type"] == "extra_forbidden":
                        parent = compatible_data
                        for key in issue["loc"][:-1]:
                            parent = parent[key]
                        parent.pop(issue["loc"][-1], None)
                try:
                    bundle_config = ProjectConfig.model_validate(compatible_data)
                except (ValueError, TypeError):
                    warnings.warn(f"Skipping evaluation with invalid config: {evaluation_dir}", stacklevel=2)
                    continue
            except (ValueError, TypeError):
                warnings.warn(f"Skipping evaluation with invalid config: {evaluation_dir}", stacklevel=2)
                continue
            created_at = (
                evaluation_metadata.get("created_at")
                if isinstance(evaluation_metadata, dict)
                else metadata.get("created_at")
                if isinstance(metadata, dict)
                else None
            ) or datetime.fromtimestamp(metrics_path.stat().st_mtime, UTC).isoformat()
            initial = Path(str((metadata or {}).get("initial_checkpoint") or "").replace("\\", "/"))
            training_run_id = run_dir.name
            cohort = str(metrics.get("cohort") or evaluation_dir.name)
            evaluation_id = (
                training_run_id
                if cohort == "validation"
                else f"{training_run_id}::{cohort}"
            )
            source_raster = metrics.get("source_raster")
            if not source_raster and isinstance(evaluation_metadata, dict):
                source_raster = evaluation_metadata.get("source_raster")
            if not source_raster:
                source_raster = bundle_config.imagery.local_raster
            bundle_raster = self._resolve_local_raster(source_raster, bundle_config)
            records.append(
                (
                    str(created_at),
                    {
                        "run_id": evaluation_id,
                        "training_run_id": training_run_id,
                        "parent_run_id": initial.parent.parent.name if initial.parent.name == "checkpoints" else None,
                        "cohort": cohort,
                        "created_at": str(created_at),
                        "dataset": bundle_config.dataset,
                        "city": bundle_config.inventory.city,
                        "backbone": metrics.get("detector") or bundle_config.model.backbone,
                        "metrics": metrics,
                        "evaluation_dir": evaluation_dir,
                        "bundle_config": bundle_config,
                        "raster_path": bundle_raster,
                    },
                )
            )
        records.sort(key=lambda item: (item[0], str(item[1]["run_id"])))
        return {str(record["run_id"]): record for _, record in records}

    def _resolve_local_raster(
        self,
        source_raster: object,
        bundle_config: ProjectConfig,
    ) -> Path:
        if source_raster:
            configured = Path(str(source_raster)).resolve()
            if configured.exists():
                return configured
            # Collected jobs retain their remote artifact-root prefix. Resolve
            # the exact frozen relative path, never another run's same basename.
            source_text = str(source_raster).replace('\\', '/')
            if '/run-inputs/' in source_text:
                candidate = (self.config.paths.root / 'run-inputs' / source_text.split('/run-inputs/', 1)[1]).resolve()
                if candidate.is_relative_to((self.config.paths.root / 'run-inputs').resolve()) and candidate.is_file():
                    return candidate
            if '/imagery/' in source_text:
                imagery_root = (self.config.paths.root / 'imagery').resolve()
                candidate = (imagery_root / source_text.split('/imagery/', 1)[1]).resolve()
                if candidate.is_relative_to(imagery_root) and candidate.is_file():
                    return candidate
            # An explicit but unavailable source is not permission to substitute
            # the sole same-named image, or the default city's current imagery.
            return configured
        if (
            bundle_config.inventory.city == self.config.inventory.city
            and bundle_config.dataset == self.config.dataset
        ):
            return self.raster_path
        return Path(str(source_raster)).resolve() if source_raster else self.raster_path

    def __len__(self) -> int:
        return len(self._runs)

    def summary(self) -> dict[str, object]:
        # One history computation at a time; concurrent readers reuse it.
        # Only stat frozen inputs to validate a cached result. Live annotations
        # are deliberately absent from these keys and remain independently live.
        with self._summary_lock:
            with self._records_lock:
                records = self.records()
                records_version = self._records_version
            paths = [record['evaluation_dir'] / name for record in records
                     for name in ('predictions.parquet', 'matches.parquet', 'ground-truth.parquet')]
            for best in (self.config.paths.root / 'benchmarks').glob('*confidence-sweep/best.json'):
                paths.extend((best, best.parent / 'provenance.json'))
            version = (records_version, self._file_versions(paths), self.selected_run_id)
            if version != self._summary_version:
                self._summary_cache = self._build_summary(records)
                self._summary_version = version
            return deepcopy(self._summary_cache)

    def _build_summary(self, records) -> dict[str, object]:
        runs = []
        from urban_tree_ml.sweep_history import best_sweeps, POLICIES
        from urban_tree_ml.temporal_studio import history_imagery
        policy_sweeps = {p: best_sweeps(self.config.paths.root, p) for p in POLICIES}
        sweeps = policy_sweeps['crown-half-radius-2-4m']
        for record in records:
            run_id = record['run_id']
            from urban_tree_ml.goal_metrics import goal_metrics
            # Legacy sweep exports identify exactly these two native cohorts.
            # A Boston temporal cohort must never borrow Boston's native sweep.
            native_cohort = 'validation' if record['city'].lower() == 'ussfo' else 'validation-usbos'
            use_sweeps = record['metrics'].get('split') == 'validation' and record['cohort'] == native_cohort
            runs.append(
                {
                    key: value
                    for key, value in record.items()
                    if key not in {"evaluation_dir", "bundle_config", "raster_path"}
                }
                | history_imagery(record)
                | {"selected": run_id == self.selected_run_id,
                   "goal_metrics": goal_metrics(record["evaluation_dir"]),
                   "best_goal_sweeps": {p: rows.get((record['training_run_id'], record['city'].lower())) for p, rows in policy_sweeps.items()}
                   if use_sweeps else {},
                   "best_goal_sweep": sweeps.get((record['training_run_id'], record['city'].lower()))
                   if use_sweeps else None}
            )
        return {"selected_run_id": self.selected_run_id, "runs": runs}

    def record(self, run_id: str | None = None):
        selected = run_id or self.selected_run_id
        if selected is not None and selected not in self._runs:
            self.records()
        if selected is None or selected not in self._runs:
            raise KeyError(f"unknown validation run {selected!r}")
        return self._runs[selected]

    def bundle(self, run_id: str | None = None) -> ModelDebugBundle:
        record = self.record(run_id)
        return self._load_bundle(record, record['evaluation_dir'])

    def review_backfill(self, record):
        directory = review_backfill_directory(record)
        return self._load_bundle(record, directory) if directory is not None else None

    def review_chip_bundle(self, run_id, chip):
        primary = self.bundle(run_id)
        if chip in primary.chip_ids:
            return primary
        extra = self.review_backfill(self.record(run_id))
        return extra if extra is not None and chip in extra.chip_ids else primary

    def _load_bundle(self, record, directory):
        selected = record['run_id']
        key = selected if directory == record['evaluation_dir'] else (selected, directory)
        from urban_tree_ml.peer_references import version
        key = (key,version(directory))
        def load():
            bundle = ModelDebugBundle(
                record["bundle_config"],
                directory,
                record["raster_path"],
                evaluation_id=selected,
                artifact_root=self.config.paths.root,
            )
            dynamic = {'_image_cache', '_target_cache', '_review_rank_arrays', '_review_rank_cache', '_collision_locations'}
            parts = {'base': retained_bytes({k: v for k, v in vars(bundle).items() if k not in dynamic})}
            parts.update({k: retained_bytes(getattr(bundle, k)) for k in dynamic if hasattr(bundle, k)})
            cache_ref = weakref.ref(self._bundles)
            def account(owner, component, size):
                cache = cache_ref()
                if cache is not None:
                    cache.resize(key, owner, component, size)
            bundle._cache_account = account
            return bundle, parts
        return self._bundles.get_or_load(key, load)

    def curation_available(self, run_id: str | None = None) -> bool:
        """Only the city/raster used by this server can receive curation writes."""
        bundle = self.bundle(run_id)
        from urban_tree_ml.resolution_recipe import same_chip_footprints
        return (
            bundle.config.inventory.city == self.config.inventory.city
            and same_chip_footprints(bundle.raster_path, bundle.config.imagery.chip_pixels,
                                     self.raster_path, self.config.imagery.chip_pixels)
        )

    def chip_comparison(
        self,
        chip_id: str,
        run_id: str | None = None,
        compare_run: str | None = None,
        preferred_run: str | None = None,
    ) -> dict[str, object]:
        full_comparison = compare_run is None
        runs: list[dict[str, object]] = []
        selected_bundle = self.bundle(run_id)
        # The editor needs model choices, not every historical score/sweep.
        records = [{k: v for k, v in record.items()
                    if k not in {'evaluation_dir', 'bundle_config', 'raster_path'}}
                   for record in self.records()]
        choices = []
        from urban_tree_ml.resolution_recipe import same_chip_footprints
        for record in records:
            metadata = self._runs[str(record['run_id'])]
            config = metadata['bundle_config']
            if (record['city'] == selected_bundle.config.inventory.city
                and record['metrics'].get('split') == selected_bundle.metrics.get('split')
                and same_chip_footprints(metadata['raster_path'], config.imagery.chip_pixels,
                                         selected_bundle.raster_path, selected_bundle.config.imagery.chip_pixels)):
                choices.append(record)
        if compare_run is not None:
            if compare_run == 'auto':
                current = self._runs[selected_bundle.evaluation_id]
                ordered = comparison_order(current, choices)
                saved = [r for r in choices if preferred_run and
                         r['training_run_id'] == preferred_run.split('::')[0]]
                saved.sort(key=lambda r: (r['run_id'] == preferred_run, r['cohort'] == current['cohort'],
                                          r['created_at']), reverse=True)
                compare_run = next((r['run_id'] for r in saved or ordered), None)
            elif compare_run not in {r['run_id'] for r in choices}:
                raise ValueError('Comparison run is not compatible with this imagery and split')
            records = [r for r in choices if r['run_id'] in {selected_bundle.evaluation_id, compare_run}]
        for record in records:
            if (
                record["city"] != selected_bundle.config.inventory.city
                or record["metrics"].get("split") != selected_bundle.metrics.get("split")
            ):
                continue
            run_id = str(record["run_id"])
            bundle = selected_bundle if run_id == selected_bundle.evaluation_id else self.bundle(run_id)
            if not same_chip_footprints(bundle.raster_path, bundle.config.imagery.chip_pixels,
                                        selected_bundle.raster_path, selected_bundle.config.imagery.chip_pixels):
                continue
            other_chip = corresponding_chip_id(chip_id, selected_bundle.config.imagery.chip_pixels,
                                               bundle.config.imagery.chip_pixels)
            if other_chip not in bundle.chip_ids:
                bundle = self.review_chip_bundle(run_id, other_chip)
            if other_chip not in bundle.chip_ids:
                runs.append({"run": record, "available": False})
                continue
            data = bundle.chip(other_chip)
            runs.append(
                {
                    "run": record,
                    "available": True,
                    "data": data,
                    "display": data["display"],
                }
            )
        available = [entry['run'] for entry in runs if entry['available']] if full_comparison else []
        return {
            "chip_id": chip_id,
            "selected_run_id": selected_bundle.evaluation_id,
            "city": selected_bundle.config.inventory.city.lower(),
            "split": selected_bundle.metrics.get('split', 'validation'),
            "choices": choices,
            "comparison_run_id": compare_run,
            "comparison_defaults": {
                row['run_id']: next((r['run_id'] for r in comparison_order(row, available)), None)
                for row in available
            } if full_comparison else {},
            "curation_available": (
                selected_bundle.config.inventory.city == self.config.inventory.city
                and same_chip_footprints(selected_bundle.raster_path, selected_bundle.config.imagery.chip_pixels,
                                         self.raster_path, self.config.imagery.chip_pixels)),
            "runs": runs,
        }


def render_studio_home(model_available: bool, run_count: int = 0) -> str:
    status = "Validation artifacts loaded" if model_available else "No validation run loaded"
    run_status = f"{run_count} validation run{'s' if run_count != 1 else ''}"
    return (
        _STUDIO_HOME.replace("__MODEL_STATUS__", status)
        .replace("__RUN_STATUS__", run_status)
    )


def inject_studio_navigation(registration_html: str) -> str:
    navigation = """
<style>.studio-nav{position:sticky;top:0;z-index:250;display:flex;flex-wrap:wrap;gap:8px;padding:10px 22px;background:#0b100df5;border-bottom:1px solid #33453a}
.studio-nav a,.studio-nav button{display:inline-flex;align-items:center;min-height:32px;color:#cce8d3;text-decoration:none;padding:5px 9px;border:1px solid #496252;border-radius:6px;background:#203027}
.studio-nav form{display:flex;gap:6px;margin-left:auto}.studio-nav input{width:190px;min-height:32px;color:#edf6ef;background:#17231c;border:1px solid #496252;border-radius:6px;padding:5px 8px}
.studio-nav+header{top:53px}.card.fullscreen{top:53px}@media(max-width:720px){.studio-nav form{width:100%;margin-left:0}.studio-nav input{flex:1}}</style>
<nav class="studio-nav"><a href="/">Studio</a><a href="/registration">Registration</a><a href="/runs">Run history</a><a href="/model">Model validation</a><form action="/curate" method="get"><input name="chip" pattern="r[0-9]{6}_c[0-9]{6}" placeholder="Validation chip ID" aria-label="Validation chip ID" required><input type="hidden" name="return" value="/registration"><button type="submit">Add chip</button></form></nav>
"""
    return registration_html.replace("<body>", f"<body>{navigation}", 1)


_STUDIO_HOME = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Urban Tree Model Studio</title><style>
:root{color-scheme:dark;font-family:Inter,ui-sans-serif,system-ui,sans-serif}*{box-sizing:border-box}
body{margin:0;background:#0d1410;color:#edf6ef}main{max-width:1050px;margin:auto;padding:54px 24px}
h1{font-size:36px;margin:0 0 8px}.lede{color:#aabdaf;margin:0 0 34px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:18px}
.card{display:block;color:inherit;text-decoration:none;background:#17231c;border:1px solid #354b3c;border-radius:14px;padding:24px;min-height:180px}
.card:hover{border-color:#78a687;transform:translateY(-1px)}h2{margin:0 0 9px}.card p{color:#b8c9bd;line-height:1.5}.status{font-size:12px;color:#8fb49a;margin-top:22px;text-transform:uppercase;letter-spacing:.06em}
</style></head><body><main><h1>Urban Tree Model Studio</h1><p class="lede">Curate labels, inspect training behavior, and debug geospatial predictions.</p>
<div class="grid"><a class="card" href="/registration"><h2>Registration curation</h2><p>Review inventory-to-imagery alignment, exclude uncertain labels, and apply per-tree corrections.</p><div class="status">Saved feedback enabled</div></a>
<a class="card" href="/runs"><h2>Run history</h2><p>Track validation metrics over time, compare compatible runs, and open any saved evaluation.</p><div class="status">__RUN_STATUS__</div></a>
<a class="card" href="/model"><h2>Model validation</h2><p>Explore loss curves, metrics, detections, misses, taxonomy errors, and DBH residuals on held-out blocks.</p><div class="status">__MODEL_STATUS__</div></a></div></main></body></html>"""


RUN_HISTORY_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Run history · Urban Tree Model Studio</title><style>
:root{color-scheme:dark;font-family:Inter,ui-sans-serif,system-ui,sans-serif}*{box-sizing:border-box}body{margin:0;background:#0d1410;color:#edf6ef}.nav{position:sticky;top:0;z-index:100;display:flex;gap:8px;padding:10px 22px;background:#090e0b;border-bottom:1px solid #2d4034}.nav a{color:#cce8d3;text-decoration:none;padding:5px 9px;border-radius:6px;background:#1b2a21}header,main{max-width:1400px;margin:auto;padding:22px 26px}h1{margin:0 0 5px}.lede,.note{color:#aabdaf}.controls{display:flex;flex-wrap:wrap;gap:12px;align-items:center;margin-top:18px}select,input,button{min-height:36px;background:#203027;color:#edf6ef;border:1px solid #486151;border-radius:7px;padding:6px 10px}.panel{background:#17231c;border:1px solid #304438;border-radius:12px;padding:16px;margin-bottom:18px}.chart{width:100%;height:250px;background:#0e1712;border-radius:8px}.table-wrap{overflow:auto}table{width:100%;border-collapse:collapse;font-size:13px}th,td{text-align:right;padding:10px;border-bottom:1px solid #304438;white-space:nowrap}th:first-child,td:first-child{text-align:left}th{color:#9eb2a4;font-weight:600}tr.selected{background:#203328}tr.city-group th{background:#0e1e14;color:#c9f5d6;text-align:left;padding:18px 12px 10px;border-top:2px solid #496252}tr.best-run{background:#233d2b}tr.best-run td:first-child{box-shadow:inset 4px 0 #91dda7}.best-badge{display:inline-block;margin-left:8px;padding:3px 7px;border:1px solid #91dda7;border-radius:10px;color:#c9f5d6;font-size:11px}a{color:#91dda7}.delta-up{color:#68db8b}.delta-down{color:#ff7b86}.muted{color:#84978a}.chip-form{display:flex;gap:8px;flex-wrap:wrap}.chip-form input{min-width:250px}@media(max-width:700px){header,main{padding:16px 10px}}
.metrics[hidden]{display:none}
</style></head><body><nav class="nav"><a href="/">Studio</a><a href="/registration">Registration</a><a href="/runs">Run history</a><a href="/model">Model validation</a></nav>
<header><h1>Evaluation history</h1><p class="lede">Internal and external validation cohorts · test remains sealed</p><div class="controls"><label>Dataset version <select id="dataset"></select></label><label>City <select id="history-city"><option value="">All cities</option></select></label><label>Match radius <select id="radius"></select></label><span id="summary" class="note"></span></div></header>
<main><section class="panel"><svg id="chart" class="chart" viewBox="0 0 900 250"></svg></section><section class="panel table-wrap"><table><thead><tr><th>Run</th><th>Imagery</th><th>Cohort</th><th>Date</th><th title="Goal-aligned AP is not yet calculated; inventory AP is not substituted.">Goal AP ⓘ</th><th>Goal F2</th><th>F1</th><th>Precision</th><th>Recall</th><th title="Correct species among matched detections with a known species label, at this row’s operating threshold.">Species accuracy</th><th>Species macro-F1</th><th>DBH MAE</th><th>Truth</th><th>Explore</th></tr></thead><tbody id="rows"></tbody></table></section>
<section class="panel"><h2>Compare one chip across runs</h2><p class="note">Open the same validation chip side by side to see which predictions moved, appeared, or disappeared.</p><form id="chip-form" class="chip-form"><input id="chip" pattern="r[0-9]{6}_c[0-9]{6}" placeholder="r000061_c000075" required><button>Compare chip</button></form></section></main>
<script>
let catalog;const $=id=>document.getElementById(id),pct=v=>v==null?'—':`${(100*v).toFixed(1)}%`,num=v=>v==null?'—':Number(v).toFixed(2),esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function compatible(){return catalog.runs.filter(run=>(!$('dataset').value||run.dataset===$('dataset').value)&&(!$('history-city').value||run.city===$('history-city').value)&&run.metrics.split!=='train').sort((a,b)=>String(a.imagery_label||a.city||'').localeCompare(String(b.imagery_label||b.city||''))||String(a.created_at||'').localeCompare(String(b.created_at||''))||a.run_id.localeCompare(b.run_id))}
function f2(d){return d?.precision==null||d?.recall==null?null:(4*d.precision+d.recall?5*d.precision*d.recall/(4*d.precision+d.recall):0)}
function swept(){return $('score-mode').value==='best'}
function policy(){return $('radius').value==='crown'?'crown-half-radius-2-4m':Number($('radius').value)===2?'fixed-2m':'fixed-4m'}
function bestSweep(run){return run.best_goal_sweeps?.[policy()]||(policy()==='crown-half-radius-2-4m'?run.best_goal_sweep:null)}
function fixedRadius(){return $('radius').value==='crown'?'2.0':$('radius').value}
function scoreLabel(run){const best=swept()?bestSweep(run):null;return best?`Swept ${best.threshold.toFixed(2)} · ${policy()}`:`Fixed fallback ${run.metrics.confidence_threshold} · ${fixedRadius()} m`}
function operatingThreshold(run){return (swept()?bestSweep(run)?.threshold:null)??run.metrics.confidence_threshold}
function comparisonKey(run){return JSON.stringify([run.imagery_key,policy(),swept()?'best':run.metrics.confidence_threshold])}
function bestRuns(runs){const best=new Map();for(const run of runs){const score=f2(metrics(run)?.detection),key=comparisonKey(run);if(score!=null&&(!best.has(key)||score>best.get(key).f2)){best.set(key,{run_id:run.run_id,f2:score,ap:metrics(run)?.detection?.average_precision})}}return best}
function columnBests(runs){const groups=new Map();for(const run of runs){const m=metrics(run),d=m?.detection||{},a=m?.attributes_on_matched_detections||{},key=comparisonKey(run),best=groups.get(key)||{};for(const [name,value] of Object.entries({ap:d.average_precision,f2:f2(d),f1:d.f1,precision:d.precision,recall:d.recall,species:a.species?.accuracy,macro:a.species?.macro_f1})){if(value!=null)best[name]=best[name]==null?value:Math.max(best[name],value)}groups.set(key,best)}return groups}
function metricCell(value,best){return `<td>${pct(value)}<br><small>${delta(value,best)}</small></td>`}
function speciesCell(species,field){
 const samples=species?.samples;
 const value=samples===0?null:species?.[field];
 const support=samples==null?'Sample count unavailable':`n=${samples.toLocaleString()} matched labeled ${samples===1?'tree':'trees'}`;
 return `<td>${pct(value)}<br><small class="muted">${esc(support)}</small></td>`;
}
function metrics(run){const best=swept()?bestSweep(run):null;if(best)return {detection:best,attributes_on_matched_detections:best.attributes_on_matched_detections};const radius=fixedRadius(),saved=run.metrics.metrics_by_match_radius_m[radius];return {...saved,detection:run.goal_metrics?.[radius]?.detection||{}}}
function delta(value,previous){if(value==null||previous==null)return '<span class="muted">—</span>';const d=value-previous,cls=d>0?'delta-up':d<0?'delta-down':'muted';return `<span class="${cls}">${d>=0?'+':''}${(100*d).toFixed(2)} pp</span>`}
function chart(){const runs=compatible(),series=[['Goal-aligned F2','#66d88a',r=>f2(metrics(r)?.detection)],['F1','#59bde8',r=>metrics(r)?.detection?.f1],['Species accuracy','#ffbc5b',r=>metrics(r)?.attributes_on_matched_detections?.species?.accuracy]],values=series.flatMap(s=>runs.map(s[2]).filter(v=>v!=null)),svg=$('chart');if(!runs.length||!values.length){svg.innerHTML='<text x="30" y="125" fill="#9eb2a4">No comparable metrics</text>';return}const lo=Math.min(...values),hi=Math.max(...values),x=i=>70+(runs.length<2?0:i/(runs.length-1)*770),y=v=>205-(v-lo)/(hi-lo||1)*150;svg.innerHTML='<line x1="70" y1="205" x2="840" y2="205" stroke="#496052"/>'+series.map(([name,color,get],j)=>{const points=runs.map((r,i)=>[x(i),get(r)]).filter(p=>p[1]!=null).map(p=>[p[0],y(p[1])].join(',')).join(' ');return `${new Set(runs.map(r=>r.city)).size<=1?`<polyline points="${points}" fill="none" stroke="${color}" stroke-width="3"/>`:points.split(" ").filter(Boolean).map(p=>{const [cx,cy]=p.split(",");return `<circle cx="${cx}" cy="${cy}" r="3" fill="${color}"/>`}).join("")}<text x="75" y="${22+j*19}" fill="${color}">${name}</text>`}).join('')+runs.map((r,i)=>`<text x="${x(i)}" y="228" fill="#9eb2a4" text-anchor="middle" font-size="10">${i+1}</text>`).join('')}
function render(){const runs=compatible();$('summary').textContent=`${runs.length} validation evaluations. Best = highest goal-aligned F2 among visible runs per city and confidence threshold at this radius, across historical cohort names. Detection metrics show their delta versus the column best. Species scores are conditional on matched, species-labeled trees, with sample counts shown; labels/chips may differ. Unmatched predictions in frozen loss-free regions are ignored, not credited. Missing mask data and goal-aligned AP show —; no inventory-score fallback. Use the frozen-label comparison report for model uplift.`;const best=bestRuns(runs),columns=columnBests(runs);$('rows').innerHTML=runs.map((run,index)=>{const m=metrics(run),d=m?.detection||{},a=m?.attributes_on_matched_detections||{},ap=d.average_precision,key=comparisonKey(run),previous=best.get(key),column=columns.get(key)||{},isBest=f2(d)!=null&&f2(d)===previous?.f2,heading=index===0||runs[index-1].imagery_key!==run.imagery_key?`<tr class="city-group"><th colspan="14" scope="rowgroup">${esc(run.imagery_label)} · ${runs.filter(r=>r.imagery_key===run.imagery_key).length} evaluations</th></tr>`:"",html=`${heading}<tr class="${run.selected?'selected':''} ${isBest?'best-run':''}"><td><strong>${index+1}. ${esc(run.training_run_id||run.run_id)}</strong>${isBest?'<span class="best-badge">Best goal F2</span>':''}<br><span class="muted">${esc(run.backbone||'')} · ${esc(run.dataset||'')}</span></td><td>${esc(run.imagery_label)}</td><td>${esc(run.cohort||'validation')}</td><td>${new Date(run.created_at).toLocaleString()}</td>${metricCell(ap,column.ap)}${metricCell(f2(d),column.f2)}${metricCell(d.f1,column.f1)}${metricCell(d.precision,column.precision)}${metricCell(d.recall,column.recall)}${speciesCell(a.species,'accuracy')}${speciesCell(a.species,'macro_f1')}<td>${a.dbh?`${num(a.dbh.mae_in)} in`:'—'}</td><td>${run.metrics.ground_truth_trees??'—'}</td><td><a href="/model?run=${encodeURIComponent(run.run_id)}">Explore</a></td></tr>`;return html}).join('');chart()}
async function init(){catalog=await fetch('/api/runs').then(r=>r.json());const datasets=[...new Set(catalog.runs.map(r=>r.dataset||'unknown'))],selected=catalog.runs.find(r=>r.run_id===catalog.selected_run_id);$('dataset').innerHTML='<option value="">All dataset versions</option>'+datasets.map(d=>`<option>${esc(d)}</option>`).join('');$('history-city').innerHTML='<option value="">All cities</option>'+[...new Set(catalog.runs.map(r=>r.city))].sort().map(c=>`<option value="${esc(c)}">${esc(catalog.runs.find(r=>r.city===c)?.city_label||c)}</option>`).join('');$('history-city').addEventListener('change',render);const radii=[...new Set(catalog.runs.flatMap(r=>Object.keys(r.metrics.metrics_by_match_radius_m||{})))].sort((a,b)=>+a-+b);$('radius').innerHTML=radii.map(r=>`<option ${r==='4.0'?'selected':''} value="${r}">${r} m</option>`).join('');$('dataset').addEventListener('change',render);$('radius').addEventListener('change',render);$('chip-form').addEventListener('submit',event=>{event.preventDefault();const run=compatible()[0]?.run_id||catalog.selected_run_id;location.href=`/compare?chip=${encodeURIComponent($('chip').value)}&run=${encodeURIComponent(run)}`});window.studioViewState?.restore();render()}init();
</script></body></html>"""


# Prefer swept operating points; explicitly label historical fixed-score fallbacks.
# Swept attributes must be recomputed at the swept threshold and matching policy.
RUN_HISTORY_HTML = RUN_HISTORY_HTML.replace(
    '<label>Match radius <select id="radius"></select></label>',
    '<label>Scores <select id="score-mode"><option value="best">Best available · swept or saved fixed</option>'
    '<option value="fixed">Saved fixed confidence · fixed radius</option></select></label>'
    '<label>Matching policy <select id="radius"></select></label>',
).replace(
    "function render(){const runs=compatible();",
    "function render(){$('radius').disabled=false;const runs=compatible();",
).replace(
    "${esc(run.cohort||'validation')}</td>",
    "${esc(run.cohort||'validation')}<br><small>${scoreLabel(run)}</small></td>",
).replace(
    "${runs.length} validation evaluations. Best = highest goal-aligned F2",
    "${runs.length} validation evaluations. ${swept()?'Confidence optimized separately for the selected matching policy; attributes use the same threshold and matches. Missing sweeps fall back to saved fixed scores, explicitly labeled (2 m for unavailable crown-aware scores). Crown-aware uses frozen target radius, with unknown radii falling back to 2 m. Native labels and operating points differ; not a controlled comparison.':'Saved fixed confidence; crown-aware selection falls back to saved 2 m.'} Best = highest goal-aligned F2",
).replace(
    "per city and confidence threshold at this radius",
    "per imagery acquisition within the selected scoring mode (fixed scores additionally grouped by confidence)",
).replace(
    "$('radius').addEventListener('change',render);",
    "$('radius').addEventListener('change',render);$('score-mode').addEventListener('change',render);",
).replace(
    'href="/model?run=${encodeURIComponent(run.run_id)}"',
    'href="/model?run=${encodeURIComponent(run.run_id)}&threshold=${operatingThreshold(run)}&radius=${fixedRadius()}"',
).replace("run.metrics.split!=='train'", "!['train','test'].includes(run.metrics.split)")
RUN_HISTORY_HTML = RUN_HISTORY_HTML.replace('pattern="r[0-9]{6}_c[0-9]{6}"',
                                           'pattern="(shift_)?r[0-9]{6,}_c[0-9]{6,}"')

# The shared header owns city selection. Standalone exports retain their selector.
RUN_HISTORY_HTML = RUN_HISTORY_HTML.replace(
    "window.studioViewState?.restore();render()}init();",
    "configureHistoryPolicy();window.studioViewState?.restore();configureHistoryCity();render()}init();",
).replace("async function init(){catalog=", """
function configureHistoryPolicy(){
 const radius=$('radius');radius.innerHTML='<option value="2.0">2 m</option><option value="4.0">4 m</option><option value="crown">Crown-aware (2–4 m)</option>';radius.value='crown';
}
function configureHistoryCity(){
 const city=new URLSearchParams(location.search).get('city');
 const header=$('studio-city'),filter=$('history-city');
 const selected=city==='all'?'':(city||header?.value||'').toUpperCase();
 filter.value=selected;
 if(header)filter.closest('label').hidden=true;
 // A dataset selection from another city must not silently empty the graph.
 if($('dataset').value&&!catalog.runs.some(r=>(!selected||r.city===selected)&&r.dataset===$('dataset').value))$('dataset').value='';
}
function chart(){
 const runs=compatible(),container=$('chart');
 const acquisitions=[...new Set(runs.map(r=>r.imagery_key))];
 if(!acquisitions.length){container.innerHTML='<p>No evaluations match these filters.</p>';return}
 container.innerHTML=acquisitions.map(acquisition=>{
  const group=runs.filter(r=>r.imagery_key===acquisition),series=[
   ['Goal-aligned F2','#66d88a',r=>f2(metrics(r)?.detection)],
   ['F1','#59bde8',r=>metrics(r)?.detection?.f1],
   ['Species accuracy','#ffbc5b',r=>metrics(r)?.attributes_on_matched_detections?.species?.accuracy]];
  const x=i=>65+(group.length<2?0:i/(group.length-1)*790),y=v=>200-v*155;
  return `<h2>${esc(group[0].imagery_label)}</h2><svg class="chart" viewBox="0 0 900 250" aria-label="${esc(group[0].imagery_label)} evaluation history">`+
   [0,.25,.5,.75,1].map(v=>`<text x="15" y="${y(v)+4}" fill="#9eb2a4" font-size="11">${pct(v)}</text>`).join('')+
   series.map(([name,color,get],j)=>{const points=group.map((r,i)=>({r,i,v:get(r)})).filter(p=>p.v!=null);return `<text x="${65+j*240}" y="20" fill="${color}">${name}</text><polyline points="${points.map(p=>`${x(p.i)},${y(p.v)}`).join(' ')}" fill="none" stroke="${color}" stroke-width="2"/>`+points.map(p=>`<circle cx="${x(p.i)}" cy="${y(p.v)}" r="3" fill="${color}"><title>${esc(p.r.training_run_id||p.r.run_id)} · ${name}: ${pct(p.v)} · ${esc(scoreLabel(p.r))}${name==='Species accuracy'?` · n=${metrics(p.r)?.attributes_on_matched_detections?.species?.samples??'unknown'} matched labeled trees`:''}</title></circle>${name==='Species accuracy'&&metrics(p.r)?.attributes_on_matched_detections?.species?.samples===1?`<text x="${x(p.i)}" y="${y(p.v)-7}" fill="${color}" text-anchor="end" font-size="11">n=1</text>`:''}`).join('')}).join('')+
   group.map((r,i)=>`<text x="${x(i)}" y="230" fill="#9eb2a4" text-anchor="middle" font-size="10">${runs.indexOf(r)+1}</text>`).join('')+'</svg>';
 }).join('');
}
async function init(){catalog=""").replace(
    '<svg id="chart" class="chart" viewBox="0 0 900 250"></svg>',
    '<div id="chart"></div>',
)


CHIP_COMPARE_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Chip comparison · Urban Tree Model Studio</title><style>
:root{color-scheme:dark;font-family:Inter,ui-sans-serif,system-ui,sans-serif}*{box-sizing:border-box}body{margin:0;background:#0d1410;color:#edf6ef}.nav{position:sticky;top:0;z-index:100;display:flex;gap:8px;padding:10px 22px;background:#090e0b;border-bottom:1px solid #2d4034}.nav a{color:#cce8d3;text-decoration:none;padding:5px 9px;border-radius:6px;background:#1b2a21}header{padding:18px 24px;border-bottom:1px solid #354b3c}h1{margin:0 0 5px}.lede,.facts{color:#aabdaf}.controls{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-top:13px}input,select,button{min-height:36px;background:#203027;color:#edf6ef;border:1px solid #486151;border-radius:7px;padding:6px 10px}.action-link{display:inline-flex;align-items:center;min-height:36px;background:#203027;color:#edf6ef!important;border:1px solid #486151;border-radius:7px;padding:6px 10px;text-decoration:none}.action-link[hidden]{display:none!important}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:16px;padding:20px}.card{background:#17231c;border:1px solid #304438;border-radius:11px;overflow:hidden}.card-head,.facts{padding:10px 12px}.card-head{display:flex;justify-content:space-between;gap:8px}.image{position:relative;aspect-ratio:1;background:#050805}.image img{width:100%;height:100%;display:block}.marker{position:absolute;transform:translate(-50%,-50%);border-radius:50%;z-index:3}.truth{width:12px;height:12px;border:2px solid #35e5ee}.truth.missed{border-color:#ff5e6c;width:15px;height:15px}.prediction{width:9px;height:9px;background:#e850e8;border:1px solid #170817;z-index:4}.prediction.matched{background:#65e486}.prediction.wrong{background:#ffb44c}.halo{position:absolute;transform:translate(-50%,-50%);border:1px dashed #d7e4da99;border-radius:50%;z-index:2}.empty{padding:50px;color:#aabdaf;text-align:center}a{color:#91dda7}
</style></head><body><nav class="nav"><a href="/">Studio</a><a href="/registration">Registration</a><a href="/runs">Run history</a><a href="/model">Model validation</a></nav><header><h1>Cross-run chip comparison</h1><p class="lede">One image, every available model output.</p><form id="form" class="controls"><input id="chip" pattern="r[0-9]{6}_c[0-9]{6}" required><label>Radius <select id="radius"><option value="2.0">2 m</option><option selected value="4.0">4 m</option></select></label><button>Load</button><a id="curate" class="action-link" href="/registration">Curate this chip</a></form></header><main id="grid" class="grid"></main>
<script>
let payload;const $=id=>document.getElementById(id),params=new URLSearchParams(location.search),requestedRun=params.get('run'),esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function match(data,display,threshold){const radius=+$('radius').value/display.resolution_m/display.output_stride,preds=data.predictions.filter(p=>p.score>=threshold).sort((a,b)=>b.score-a.score),used=new Set(),pairs=new Map();for(const p of preds){let best=null,dist=Infinity;for(const t of data.ground_truth){if(used.has(t.tree_id))continue;const d=Math.hypot(p.output_x-t.output_x,p.output_y-t.output_y);if(d<=radius&&d<dist){best=t;dist=d}}if(best){used.add(best.tree_id);pairs.set(p.prediction_id,best)}}return{preds,used,pairs}}
function layers(entry,m){const d=entry.display,size=d.chip_pixels,stride=d.output_stride,diameter=200*+$('radius').value/d.resolution_m/size,at=(v,a)=>`${100*v[a]*stride/size}%`,halos=entry.data.ground_truth.map(t=>`<i class="halo" style="left:${at(t,'output_x')};top:${at(t,'output_y')};width:${diameter}%;height:${diameter}%"></i>`).join(''),truth=entry.data.ground_truth.map(t=>`<i class="marker truth ${m.used.has(t.tree_id)?'':'missed'}" title="${esc(t.species||t.tree_id)}" style="left:${at(t,'output_x')};top:${at(t,'output_y')}"></i>`).join(''),preds=m.preds.map(p=>{const t=m.pairs.get(p.prediction_id),wrong=t&&t.species_id!=null&&p.species_id!=null&&p.species_id!==t.species_id;return `<i class="marker prediction ${t?(wrong?'wrong':'matched'):''}" title="${esc(p.species)} · ${(100*p.score).toFixed(0)}%" style="left:${at(p,'output_x')};top:${at(p,'output_y')}"></i>`}).join('');return halos+truth+preds}
function render(){if(!payload)return;$('grid').innerHTML=payload.runs.map(entry=>{const run=entry.run,label=`${run.city||'—'} · ${run.cohort||'validation'} · ${run.training_run_id||run.run_id}`;if(!entry.available)return `<article class="card"><div class="card-head"><strong>${esc(label)}</strong></div><div class="empty">Chip unavailable in this evaluation</div></article>`;const threshold=+run.metrics.confidence_threshold,m=match(entry.data,entry.display,threshold),wrong=[...m.pairs].filter(([id,t])=>{const p=m.preds.find(v=>v.prediction_id===id);return t.species_id!=null&&p.species_id!=null&&p.species_id!==t.species_id}).length;return `<article class="card"><div class="card-head"><strong>${esc(label)}</strong><a href="/model?run=${encodeURIComponent(run.run_id)}&chip=${encodeURIComponent(payload.chip_id)}">Open evaluation</a></div><div class="image"><img src="/api/model/image/${encodeURIComponent(payload.chip_id)}.png?run=${encodeURIComponent(run.run_id)}">${layers(entry,m)}</div><div class="facts">${m.pairs.size} matched · ${entry.data.ground_truth.length-m.used.size} missed · ${m.preds.length-m.pairs.size} false positive · ${wrong} wrong species · threshold ${threshold.toFixed(2)}</div></article>`}).join('')}
async function load(){const chip=$('chip').value,runQuery=requestedRun?`&run=${encodeURIComponent(requestedRun)}`:'';history.replaceState(null,'',`/compare?chip=${encodeURIComponent(chip)}${runQuery}`);$('grid').innerHTML='<div class="empty">Loading evaluation outputs…</div>';const response=await fetch(`/api/runs/chip/${encodeURIComponent(chip)}?${runQuery.slice(1)}`);payload=await response.json();if(!response.ok){$('grid').innerHTML=`<div class="empty">${esc(payload.error)}</div>`;return}$('curate').hidden=!payload.curation_available;const returnTo=location.pathname+location.search;$('curate').href=`/curate?run=${encodeURIComponent(payload.selected_run_id||'')}&chip=${encodeURIComponent(chip)}&return=${encodeURIComponent(returnTo)}`;render()}
$('chip').value=params.get('chip')||'';$('form').addEventListener('submit',e=>{e.preventDefault();load()});$('radius').addEventListener('change',render);window.studioViewState?.restore();if($('chip').value)load();
</script></body></html>"""


from urban_tree_ml.compare_overlay import COMPARE_OVERLAY

CHIP_COMPARE_HTML = CHIP_COMPARE_HTML.replace(
    '</body>', COMPARE_OVERLAY + '</body>'
).replace('const threshold=+run.metrics.confidence_threshold,',
          'const threshold=overlayThreshold(),').replace('pattern="r[0-9]{6}_c[0-9]{6}"',
          'pattern="(shift_)?r[0-9]{6,}_c[0-9]{6,}"')
CHIP_COMPARE_HTML = CHIP_COMPARE_HTML.replace(
    '`/curate?run=${encodeURIComponent(payload.selected_run_id',
    "`${payload.split==='train'?'/curate-training':'/curate'}?city=${encodeURIComponent(payload.city)}&run=${encodeURIComponent(payload.selected_run_id",
).replace('encodeURIComponent(payload.chip_id)}.png?run=${encodeURIComponent(b.run.run_id)',
          'encodeURIComponent(b.data.chip_id)}.png?run=${encodeURIComponent(b.run.run_id)')

MODEL_DEBUG_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Model validation · Urban Tree Model Studio</title><style>
:root{color-scheme:dark;font-family:Inter,ui-sans-serif,system-ui,sans-serif}*{box-sizing:border-box}
body{margin:0;background:#0d1410;color:#edf6ef}.nav{position:sticky;top:0;z-index:100;display:flex;gap:8px;padding:10px 22px;background:#090e0b;border-bottom:1px solid #2d4034}.nav a{color:#cce8d3;text-decoration:none;padding:5px 9px;border-radius:6px;background:#1b2a21}
header{position:sticky;top:45px;z-index:5;background:#142019ee;backdrop-filter:blur(12px);padding:18px 24px;border-bottom:1px solid #354b3c}h1{margin:0 0 5px;font-size:23px}.lede{margin:0;color:#aabdaf}.metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(135px,1fr));gap:10px;padding:18px 24px}.metric,.panel{background:#17231c;border:1px solid #304438;border-radius:10px;padding:13px}.metric strong{display:block;font-size:24px}.metric span{font-size:12px;color:#9eb2a4}.workspace{display:grid;grid-template-columns:minmax(300px,1fr) minmax(460px,2fr);gap:16px;padding:0 24px 24px}.panel h2{margin:0 0 12px;font-size:16px}.chart{width:100%;height:190px;background:#0e1712;border-radius:7px}.controls{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin-top:12px}select,input{accent-color:#69be83;background:#203027;color:#edf6ef;border:1px solid #486151;border-radius:6px;padding:6px}.action-link{display:inline-flex;align-items:center;min-height:36px;background:#203027;color:#edf6ef!important;border:1px solid #486151;border-radius:7px;padding:6px 10px;text-decoration:none}.action-link[hidden]{display:none!important}.gallery{display:grid;grid-template-columns:repeat(auto-fill,minmax(275px,1fr));gap:14px;padding:0 24px 30px}.card{background:#17231c;border:1px solid #304438;border-radius:10px;overflow:hidden}.card-head{display:flex;justify-content:space-between;padding:9px 11px;font-size:12px;color:#b9cabe}.image{position:relative;aspect-ratio:1;background:#050805;cursor:zoom-in}.image img{width:100%;height:100%;display:block}.marker{position:absolute;transform:translate(-50%,-50%);border-radius:50%;pointer-events:auto;cursor:help;z-index:3}.match-radius{position:absolute;transform:translate(-50%,-50%);border:1px dashed #d7e4da99;border-radius:50%;pointer-events:none;z-index:2}.truth{width:11px;height:11px;border:2px solid #35e5ee}.truth.missed{border-color:#ff5e6c;width:14px;height:14px}.prediction{width:8px;height:8px;background:#e850e8;border:1px solid #170817;z-index:4}.prediction.matched{background:#65e486}.prediction.wrong{background:#ffb44c}.facts{padding:9px 11px;color:#aebfb3;font-size:12px;line-height:1.5}.legend{display:flex;flex-wrap:wrap;gap:5px 12px;font-size:12px;color:#b6c8bb}.legend-item{display:inline-flex;align-items:center;gap:5px;cursor:help}.dot{display:inline-block;width:10px;height:10px;border-radius:50%}.cyan{border:2px solid #35e5ee}.green{background:#65e486}.orange{background:#ffb44c}.pink{background:#e850e8}.red{border:2px solid #ff5e6c}.halo{border:1px dashed #d7e4da}.empty{padding:50px;text-align:center;color:#afc1b4}.tooltip{position:fixed;display:none;z-index:130;max-width:280px;white-space:pre-line;padding:8px 10px;border-radius:7px;background:#060a08ee;border:1px solid #58705f;color:#eef7f0;font-size:12px;line-height:1.4;pointer-events:none;box-shadow:0 8px 28px #0009}.modal{position:fixed;inset:45px 0 0;z-index:120;display:grid;place-items:center;padding:24px;background:#030604e8}.modal[hidden]{display:none}.modal-panel{width:min(92vw,900px);max-height:calc(100vh - 60px);overflow:auto;background:#142019;border:1px solid #496252;border-radius:14px;box-shadow:0 20px 70px #000}.modal-head{display:flex;justify-content:space-between;align-items:center;padding:12px 15px}.modal-head h2{margin:0;font-size:17px}.modal-close{background:#26392d;color:#edf6ef;border:1px solid #56705e;border-radius:7px;padding:6px 10px;cursor:pointer}.modal .image{width:min(86vw,78vh,800px);margin:auto}.modal .match-radius{border-width:2px}.modal .prediction{width:10px;height:10px}.modal-note{padding:12px 16px 17px;color:#b9cabe}@media(max-width:850px){.workspace{grid-template-columns:1fr}.gallery{padding:0 10px}.metrics,header{padding-left:12px;padding-right:12px}}
</style></head><body><nav class="nav"><a href="/">Studio</a><a href="/registration">Registration</a><a href="/runs">Run history</a><a href="/model">Model validation</a></nav>
<header><h1>Validation explorer</h1><p class="lede">Held-out validation blocks only · test remains sealed</p><div class="controls"><label>Radius <select id="radius" title="Maximum center-to-center distance for a spatial match"></select></label><label>Confidence <input id="threshold" type="number" min="0" max="1" step="0.01" required style="width:80px" title="Only predictions at or above this center confidence are shown"><span id="threshold-value" hidden></span></label><label>Order <select id="sort"><option selected value="worst">Worst detection F1</option><option value="missed">Most missed</option><option value="false_positive">Most false positives</option><option value="species_errors">Most species errors</option><option value="matched">Most matches</option></select></label><label>Cards <select id="limit"><option>12</option><option selected>24</option><option>48</option><option>97</option></select></label><label>Review status <select id="review-status"><option value="all">All statuses</option><option value="pending">Unreviewed</option><option value="not-final">Not final reviewed</option><option value="second-pending">First pass only</option><option value="done">Done (any pass)</option><option value="more-done">Final reviewed</option></select></label><span class="legend"><span class="legend-item" title="Retained municipal inventory tree"><i class="dot cyan"></i>Inventory truth</span><span class="legend-item" title="Prediction within the selected radius and with the correct species"><i class="dot green"></i>Matched</span><span class="legend-item" title="Prediction within the selected radius but with a different species"><i class="dot orange"></i>Wrong species</span><span class="legend-item" title="Prediction with no unused inventory tree inside the selected radius"><i class="dot pink"></i>False positive</span><span class="legend-item" title="Inventory tree with no prediction inside the selected radius"><i class="dot red"></i>Missed</span><span class="legend-item" title="The selected 2 m or 4 m matching tolerance around each inventory point"><i class="dot halo"></i>Match radius</span></span></div></header>
<style>#review-load{margin:16px 24px;padding:14px 18px;border:1px solid #486151;border-radius:8px;background:#203027;display:flex;gap:12px;align-items:center}#review-load[hidden]{display:none}#review-load::before{content:'';width:16px;height:16px;border:2px solid #486151;border-top-color:#a4dfb4;border-radius:50%;animation:review-spin 1s linear infinite}#review-load.failed::before{display:none}#gallery[aria-busy="true"]{opacity:.55;pointer-events:none}@keyframes review-spin{to{transform:rotate(360deg)}}@media(prefers-reduced-motion:reduce){#review-load::before{animation:none}}</style>
<div id="review-load" role="status" aria-live="polite"><span id="review-load-text">Loading chip catalog…</span><button id="review-retry" type="button" class="action-link" hidden onclick="location.reload()">Retry</button></div>
<section id="metrics" class="metrics"></section><section class="workspace"><div class="panel"><h2>Whole-run training and validation loss</h2><p id="loss-run" class="lede"></p><p class="lede">All logged training and validation batches for this model. City, split, confidence, and chip filters do not change these curves.</p><svg id="curve" class="chart" viewBox="0 0 600 190" role="img" aria-label="Whole-run training and validation loss"></svg></div><div class="panel"><h2>What this view answers</h2><p>Do detections land on inventory stems? Are failures spatial or taxonomic? Does DBH remain plausible? The dashed halo shows the selected center-matching tolerance. Click any chip for a larger inspection view.</p><p id="run-detail"></p></div></section><main id="gallery" class="gallery"></main>
<div id="modal" class="modal" hidden><section class="modal-panel"><div class="modal-head"><h2 id="modal-title"></h2><span><a id="compare-chip" class="action-link" href="/compare">Compare runs</a> <a id="curate-chip" class="action-link" href="/curate">Curate this chip</a> <button id="modal-close" class="modal-close" type="button">Close</button></span></div><div id="modal-image" class="image"></div><div id="modal-note" class="modal-note"></div></section></div><div id="tooltip" class="tooltip" role="tooltip"></div>
<script>
let state,curationStatus={};const pageParams=new URLSearchParams(location.search),requestedRun=pageParams.get('run'),chipCache=new Map(),$=id=>document.getElementById(id),withRun=path=>{const url=new URL(path,location.origin);if(requestedRun)url.searchParams.set('run',requestedRun);return url.pathname+url.search},pct=v=>v==null?'—':`${(100*v).toFixed(1)}%`,num=v=>v==null?'—':Number(v).toFixed(2),escapeHtml=value=>String(value).replace(/[&<>"']/g,char=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char]));
document.head.insertAdjacentHTML('beforeend','<style>.card-head{flex-wrap:wrap;gap:6px}.assignment-tag{font-size:11px;border:1px solid #679a89;border-radius:12px;padding:3px 7px;color:#c9eada;background:#203c32}#assignment-progress{font-size:12px;color:#b9d1c7}</style>');
document.querySelector('header .controls').insertAdjacentHTML('beforeend','<label>Assignment <select id="assignment"><option value="all">All chips</option><option value="assigned">All assigned</option><option value="representative audit">Representative audit</option><option value="diagnostic">Diagnostic</option></select></label><span id="assignment-progress"></span>');
// Hydrate before any request. URL wins over saved preferences; loaders never
// restore again, so edits made while a response is pending remain authoritative.
let reviewFiltersChanged=null;
const reviewFilterIds=['radius','threshold','sort','limit','review-status','assignment'];
function setReviewOptions(id,values,label=value=>value){
 const el=$(id),previous=el.value;
 el.replaceChildren(...values.map(value=>new Option(label(value),value)));
 const match=values.find(value=>value===previous||(id==='radius'&&Number(value)===Number(previous)));
 if(match!==undefined)el.value=match;
}
function hydrateReviewFilters(){
 setReviewOptions('radius',['2.0','4.0'],v=>`${v} m`);
 $('threshold').placeholder='Loading…';
 for(const id of reviewFilterIds){
  const el=$(id);let value=window.studioViewState?.initialValue(id)??pageParams.get(id);
  if(id==='review-status'&&value==null&&pageParams.get('unreviewed')==='1')value='pending';
  if(value==null)continue;
  if(id==='radius'){
   if(!Number.isFinite(Number(value))||Number(value)<=0)continue;
   value=String(Number(value).toFixed(1));
  }
  if(id==='threshold'&&(value===''||!Number.isFinite(Number(value))||Number(value)<0||Number(value)>1))continue;
  if(el.tagName==='SELECT'&&![...el.options].some(o=>o.value===value)){
   if(!['radius','assignment'].includes(id))continue;
   el.add(new Option(id==='radius'?`${value} m`:value,value));
  }
  el.value=value;
 }
 $('threshold-value').textContent=$('threshold').value;
}
function applyReviewFilterDefaults(summary){
 setReviewOptions('radius',Object.keys(summary.metrics.metrics_by_match_radius_m),v=>`${v} m`);
 if($('threshold').value==='')$('threshold').value=summary.metrics.confidence_threshold;
 $('threshold-value').textContent=Number($('threshold').value).toFixed(2);
}
hydrateReviewFilters();
document.addEventListener('change',event=>{
 if(!reviewFilterIds.includes(event.target.id))return;
 const url=new URL(location.href);
 for(const id of reviewFilterIds)if($(id).value!=='')url.searchParams.set(id,$(id).value);
 url.searchParams.delete('unreviewed');history.replaceState(null,'',url);
 reviewFiltersChanged?.();
});
$('threshold').addEventListener('input',()=>{$('threshold-value').textContent=$('threshold').value});
function metric(label,value){return `<div class="metric"><strong>${value}</strong><span>${label}</span></div>`}
function showMetrics(){if(state.metrics.prediction_only){$('metrics').textContent='Prediction snapshot — inventory references, where available, rank missed reference trees only. Accuracy and false positives are not validated.';return}const radius=$('radius').value,m=state.metrics.metrics_by_match_radius_m[radius],d=state.goal_metrics?.[radius]?.detection||{},a=m.attributes_on_matched_detections,saved=Number(state.metrics.confidence_threshold).toFixed(2);$('metrics').innerHTML=metric(`goal precision @ ${saved}`,pct(d.precision))+metric(`recall @ ${saved}`,pct(d.recall))+metric(`goal F1 @ ${saved}`,pct(d.f1))+metric('goal-aligned F2',pct(d.f2))+metric('species accuracy',pct(a.species?.accuracy))+metric('species macro-F1',pct(a.species?.macro_f1))+metric('DBH MAE',a.dbh?`${num(a.dbh.mae_in)} in`:'—')+metric('joint recall',pct(a.joint?.recall));}
function chart(summary=state){const svg=$('curve'),curves=summary.training_curves||{},series=[['train/loss_epoch','#68db8b'],['validation/loss','#ffbc5b']];$('loss-run').textContent=summary.training_run_id||summary.run_id||'';let values=series.flatMap(([tag])=>(curves[tag]||[]).map(x=>x.value));if(!values.length){svg.innerHTML='<text x="20" y="95" fill="#9eb2a4">No recorded loss curves for this model</text>';return}const lo=Math.min(...values),hi=Math.max(...values),x=(i,n)=>35+(n<2?0:i/(n-1)*530),y=v=>155-(v-lo)/(hi-lo||1)*120;svg.innerHTML='<line x1="35" y1="155" x2="565" y2="155" stroke="#496052"/>'+series.map(([tag,color],j)=>{const a=curves[tag]||[],points=a.map((p,i)=>`${x(i,a.length)},${y(p.value)}`).join(' ');return `<polyline points="${points}" fill="none" stroke="${color}" stroke-width="3"/><text x="40" y="${20+j*18}" fill="${color}">${tag.replace('/loss_epoch','').replace('/loss','')}</text>`}).join('');}
function matchChip(data){const threshold=+$('threshold').value,radius=+$('radius').value/state.display.resolution_m/state.display.output_stride,preds=data.predictions.filter(p=>p.score>=threshold).sort((a,b)=>b.score-a.score),used=new Set(),pairs=new Map(),distances=new Map();for(const p of preds){let best=null,dist=Infinity;for(const t of data.ground_truth){if(used.has(t.tree_id))continue;const d=Math.hypot(p.output_x-t.output_x,p.output_y-t.output_y);if(d<=radius&&d<dist){best=t;dist=d}}if(best){used.add(best.tree_id);pairs.set(p.prediction_id,best);distances.set(p.prediction_id,dist*state.display.output_stride*state.display.resolution_m)}}return {preds,pairs,used,distances}}
function nearestPredictionM(t,m){return m.preds.length?Math.min(...m.preds.map(p=>Math.hypot(p.output_x-t.output_x,p.output_y-t.output_y)))*state.display.output_stride*state.display.resolution_m:null}
function titleTruth(t,matched,nearestM){const source=t.inventory_species||t.species,training=t.species?`Species target: ${t.species}${t.genus?` / genus: ${t.genus}`:''}`:t.genus?`Genus-only target: ${t.genus} (species outside model vocabulary)`:'Detection-only target (no supported taxonomy label)';return [`Inventory truth: ${t.tree_id}`,source?`Inventory species: ${source}`:'Inventory species unavailable',training,t.dbh_in==null?'DBH unavailable':`${t.dbh_in.toFixed(1)} in DBH`,matched?'Matched inside selected radius':'Missed at selected radius',nearestM==null?'No displayed predictions':`Nearest displayed prediction: ${nearestM.toFixed(1)} m`].join(' · ')}
function supervisionText(p){const s=p.center_supervision;if(!s)return 'Center supervision unavailable';const weight=s.would_penalize?` · ${(100*s.negative_weight).toFixed(0)}% negative weight`:'';return `Validation target: ${s.label}${weight}${s.exact?'':' · reconstructed estimate'}`}
function supervisionStyle(p){const status=p.center_supervision?.status,rule=status==='negative'?'2px solid #ff4055':status==='ignored'?'2px solid #35e5ee':status==='near-positive'?'2px dashed #ffd166':status==='positive'?'2px solid #65e486':'2px dotted #d7e4da';return `outline:${rule};outline-offset:2px`}
function titlePrediction(p,t,wrong,distanceM,predictionOnly=false){return [`Prediction: ${p.species||'Tree (species not predicted)'}`,p.genus_species_disagreement?`⚠ Genus ${p.genus} disagrees with species (expected ${p.species_expected_genus})`:null,p.species_confidence==null?'Species confidence unavailable':`${(100*p.species_confidence).toFixed(0)}% species confidence`,p.dbh_in==null?'DBH not predicted':`${p.dbh_in.toFixed(1)} in DBH`,`${(100*p.score).toFixed(0)}% detection confidence`,predictionOnly?'No ground truth — match status unavailable':t?(wrong?`Spatial match to ${t.tree_id} at ${distanceM.toFixed(1)} m, wrong species`:`Matched to ${t.tree_id} at ${distanceM.toFixed(1)} m`):'No truth inside selected radius',predictionOnly?'Unscored; not a background label':supervisionText(p)].filter(Boolean).join(' · ')}
function chipStats(data,m){const wrong=[...m.pairs].filter(([id,t])=>{const p=m.preds.find(value=>value.prediction_id===id);return t.species_id!=null&&p.species_id!=null&&p.species_id!==t.species_id}).length,unmatched=m.preds.filter(p=>!m.pairs.has(p.prediction_id)),penalized=unmatched.filter(p=>p.center_supervision?.would_penalize).length,ignored=unmatched.filter(p=>p.center_supervision?.status==='ignored').length,unknown=unmatched.length-penalized-ignored;return {matched:m.pairs.size,missed:data.ground_truth.length-m.used.size,falsePositive:unmatched.length,penalized,ignored,unknown,wrong}}
function markerLayers(data,m){const size=state.display.chip_pixels,stride=state.display.output_stride,radiusM=+$('radius').value,radiusDiameter=200*radiusM/state.display.resolution_m/size;const at=(value,axis)=>`${100*value[axis]*stride/size}%`,halos=data.ground_truth.map(t=>`<i class="match-radius" style="left:${at(t,'output_x')};top:${at(t,'output_y')};width:${radiusDiameter}%;height:${radiusDiameter}%"></i>`).join(''),truth=data.ground_truth.map(t=>{const matched=m.used.has(t.tree_id),tip=escapeHtml((data.provisional_reference?'City inventory reference · ':'')+titleTruth(t,matched,nearestPredictionM(t,m)));return `<i tabindex="0" class="marker truth ${matched?'':'missed'}" style="left:${at(t,'output_x')};top:${at(t,'output_y')}" data-tip="${tip}" title="${tip}"></i>`}).join(''),predictions=m.preds.map(p=>{const t=m.pairs.get(p.prediction_id),wrong=t&&t.species_id!=null&&p.species_id!=null&&p.species_id!==t.species_id,tip=escapeHtml(titlePrediction(p,t,wrong,m.distances.get(p.prediction_id),data.prediction_only));return `<i tabindex="0" class="marker prediction ${t?(wrong?'wrong':'matched'):''}" style="left:${at(p,'output_x')};top:${at(p,'output_y')};${data.prediction_only?'outline:2px dashed #d7e4da':supervisionStyle(p)}" data-tip="${tip}" title="${tip}"></i>`}).join('');return halos+truth+predictions}
const centerMarkerLayers=markerLayers;
markerLayers=function(data,m){const size=state.display.chip_pixels,stride=state.display.output_stride,res=state.display.resolution_m;
const circles=(trees,kind)=>trees.map(t=>{const c=window.estimateCrown?.(t);if(!c)return '';const d=100*c.width_m/res/size;return `<i class="crown-circle ${kind}" style="left:${100*t.output_x*stride/size}%;top:${100*t.output_y*stride/size}%;width:${d}%;height:${d}%"></i>`}).join('');
return circles(data.ground_truth,'inventory')+circles(m.preds,'predicted')+centerMarkerLayers(data,m)};
const originalTitleTruth=titleTruth,originalTitlePrediction=titlePrediction;
titleTruth=(...args)=>originalTitleTruth(...args)+' · '+(window.crownDescription?.(args[0])||'');
titlePrediction=(...args)=>originalTitlePrediction(...args)+' · '+(window.crownDescription?.(args[0])||'');
function showTooltip(event){const tip=$('tooltip');tip.textContent=event.currentTarget.dataset.tip;tip.style.display='block';moveTooltip(event)}
function moveTooltip(event){const tip=$('tooltip'),clientX=Number.isFinite(event.clientX)?event.clientX:window.innerWidth/2,clientY=Number.isFinite(event.clientY)?event.clientY:window.innerHeight/2,x=Math.min(clientX+14,window.innerWidth-300),y=Math.min(clientY+14,window.innerHeight-tip.offsetHeight-12);tip.style.left=`${Math.max(8,x)}px`;tip.style.top=`${Math.max(8,y)}px`}
function bindTooltips(root){root.querySelectorAll('[data-tip]').forEach(marker=>{marker.addEventListener('pointerenter',showTooltip);marker.addEventListener('pointermove',moveTooltip);marker.addEventListener('pointerleave',()=>{$('tooltip').style.display='none'});marker.addEventListener('focus',showTooltip);marker.addEventListener('blur',()=>{$('tooltip').style.display='none'})})}
function supervisionSummary(stats){return `${stats.falsePositive} unmatched (${stats.penalized} penalized · ${stats.ignored} ignored${stats.unknown?` · ${stats.unknown} unknown`:''})`}
function openModal(data){const m=matchChip(data),stats=chipStats(data,m),url=new URL(location.href);$('modal').dataset.chip=data.chip_id;$('modal-title').textContent=`${data.chip_id} · enlarged inspection`;$('modal-image').innerHTML=`<img src="${withRun(`/api/model/image/${data.chip_id}.png`)}">${markerLayers(data,m)}`;$('modal-note').textContent=data.provisional_reference?`${stats.missed} missed reference trees against the city inventory · verify changed trees for this year`:data.prediction_only?'Prediction only — no ground truth; not scored':`${$('radius').value} m radius · ${$('threshold').value} confidence · ${stats.matched} matched · ${stats.missed} missed · ${supervisionSummary(stats)} · ${stats.wrong} wrong species`;$('compare-chip').href=`/compare?chip=${encodeURIComponent(data.chip_id)}&run=${encodeURIComponent(state.run_id)}`;url.searchParams.set('chip',data.chip_id);history.replaceState(null,'',url);const returnTo=url.pathname+url.search;$('curate-chip').href=`/curate?run=${encodeURIComponent(state.run_id)}&chip=${encodeURIComponent(data.chip_id)}&threshold=${encodeURIComponent($('threshold').value)}&return=${encodeURIComponent(returnTo)}`;$('modal').hidden=false;bindTooltips($('modal-image'))}
function closeModal(){const url=new URL(location.href);url.searchParams.delete('chip');history.replaceState(null,'',url);$('modal').hidden=true;$('tooltip').style.display='none'}
function matchesReviewStatus(status){const done=!!status?.reviewed,final=!!status?.more_done;return ({all:true,pending:!done&&!final,'not-final':!final,'second-pending':done&&!final,done:done||final,'more-done':final})[$('review-status').value]??true;}
function validationQueue(){const assignments=state.assignments?.chips||{},queue=$('assignment').value,key=$('sort').value;return state.chips.filter(c=>(matchesReviewStatus(curationStatus[c.chip_id]))&&(queue==='all'||(queue==='assigned'?!!assignments[c.chip_id]:assignments[c.chip_id]?.queue===queue))).sort((a,b)=>key==='worst'?a.detection_f1-b.detection_f1||b.error_score-a.error_score:b[key]-a[key]).map(c=>c.chip_id)}
function curateFromValidation(chip){const returnUrl=new URL(location.href);returnUrl.searchParams.delete('chip');const url=new URL('/curate',location.origin);url.searchParams.set('run',state.run_id);url.searchParams.set('chip',chip);url.searchParams.set('threshold',$('threshold').value);url.searchParams.set('return',returnUrl.pathname+returnUrl.search);try{const id=crypto.randomUUID(),chips=validationQueue();if(!chips.includes(chip))chips.push(chip);sessionStorage.setItem('validation-curation:'+id,JSON.stringify({run:state.run_id,chips,returnTo:returnUrl.pathname+returnUrl.search,threshold:$('threshold').value}));url.searchParams.set('queue',id);url.searchParams.set('queue_chip',chip)}catch(error){alert('Could not save the validation queue. Please enable browser session storage to use queue navigation.');return}location.href=url.pathname+url.search}
document.addEventListener('click',event=>{const link=event.target.closest('[data-curate-chip],#curate-chip');if(!link||!state?.curation_available)return;event.preventDefault();curateFromValidation(link.dataset.curateChip||$('modal').dataset.chip)});
async function card(summary,signal){let data=chipCache.get(summary.chip_id);if(!data){data=await fetch(withRun(`/api/model/chip/${summary.chip_id}`),{signal}).then(response=>response.json());chipCache.set(summary.chip_id,data)}const m=matchChip(data),stats=chipStats(data,m);return `<article class="card"><div class="card-head"><strong>${summary.chip_id}</strong><span>${data.provisional_reference?'City inventory references':data.prediction_only?'Prediction only · no ground truth':`${data.ground_truth.length} trees · F1 ${(100*summary.detection_f1).toFixed(0)}%`}</span>${state.curation_available?`<button type="button" class="action-link" data-curate-chip="${summary.chip_id}" title="Curate this chip, then navigate the filtered validation queue">Curate</button>`:''}</div><div class="image" data-chip="${summary.chip_id}" title="Click to inspect this chip"><img loading="lazy" src="${withRun(`/api/model/image/${summary.chip_id}.png`)}">${markerLayers(data,m)}</div><div class="facts">${data.provisional_reference?`${stats.missed} missed reference trees · ${data.ground_truth.length} inventory trees`:data.prediction_only?`${m.preds.length} predictions · unscored`: `${stats.matched} matched · ${stats.missed} missed · ${supervisionSummary(stats)} · ${stats.wrong} wrong species`}</div></article>`}
function reviewLoading(message){
 const status=$('review-load');status.hidden=false;status.classList.remove('failed');
 $('review-load-text').textContent=message;$('review-retry').hidden=true;
 $('gallery').setAttribute('aria-busy','true');
}
function reviewLoaded(){ $('review-load').hidden=true;$('gallery').setAttribute('aria-busy','false'); }
function reviewLoadError(error){
 reviewLoading(error?.message||'Could not load chip review.');
 $('review-load').classList.add('failed');$('review-retry').hidden=false;
 $('gallery').setAttribute('aria-busy','false');
}
// Fetch counts only, never the entire prediction collection into the browser.
const reviewRankRequests=new Map();
let overlayController, rankingController, rankingSettings;
function startOverlayRender(){
 overlayController?.abort();
 overlayController=new AbortController();
 const settings=JSON.stringify([$('threshold').value,$('radius').value]);
 if(settings!==rankingSettings){
  rankingController?.abort();
  rankingController=new AbortController();
  rankingSettings=settings;
 }
 return overlayController.signal;
}
async function rankedReviewChips(combined=false,signal){
 if(!$('threshold').checkValidity()){ $('threshold').reportValidity(); throw Error('Enter confidence between 0 and 1 (for example 0.13).'); }
 const threshold=$('threshold').value,radius=$('radius').value;
 $('threshold-value').textContent=Number(threshold).toFixed(2);
 const runs=combined?[...new Set(state.chips.map(c=>c.inference_run).filter(Boolean))]:[state.run_id];
 const results=new Map();
 for(const run of runs){
  const key=new URLSearchParams({run,threshold,radius}).toString();
  signal?.throwIfAborted();
  const previous=reviewRankRequests.get(key);
  if(!previous || previous.signal?.aborted){
   const rankSignal=rankingController?.signal;
   const request=fetch('/api/model/ranking?'+key,{signal:rankSignal}).then(async response=>{
    if(!response.ok)throw Error('Could not calculate ranking; retry or change confidence.');
    return new Map((await response.json()).chips.map(c=>[c.chip_id,c]));
   }).then(value=>{
    if(reviewRankRequests.get(key)?.request===request)reviewRankRequests.set(key,{request});
    return value;
   }).catch(error=>{if(reviewRankRequests.get(key)?.request===request)reviewRankRequests.delete(key);throw error});
   reviewRankRequests.set(key,{request,signal:rankSignal});
   if(reviewRankRequests.size>16)reviewRankRequests.delete(reviewRankRequests.keys().next().value);
  }
  results.set(run,await reviewRankRequests.get(key).request);
 }
 return state.chips.map(c=>({...c,...results.get(combined?c.inference_run:state.run_id)?.get(c.chip_id)}));
}
let galleryGeneration=0;
async function gallery(){
 const signal=startOverlayRender();
 const generation=++galleryGeneration,key=$('sort').value,limit=+$('limit').value,assignments=state.assignments?.chips||{},queue=$('assignment').value;
 $('assignment-progress').textContent='Calculating ranking at selected confidence and radius…';
 reviewLoading('Ranking chips at the selected confidence and radius…');
 let ranked;
 try{ranked=await rankedReviewChips(false,signal)}catch(error){if(generation===galleryGeneration){$('assignment-progress').textContent=error.message;reviewLoadError(error)}return}
 if(generation!==galleryGeneration)return;
 state.chips=ranked;
 const assigned=state.chips.filter(chip=>{const a=assignments[chip.chip_id];return queue==='all'||(queue==='assigned'?!!a:a?.queue===queue)});
 const eligible=assigned.filter(chip=>matchesReviewStatus(curationStatus[chip.chip_id]));
 const chips=[...eligible].sort((a,b)=>key==='worst'?a.detection_f1-b.detection_f1||b.error_score-a.error_score:b[key]-a[key]).slice(0,limit);
 $('assignment-progress').textContent=`${assigned.filter(c=>curationStatus[c.chip_id]?.reviewed).length}/${assigned.length} reviewed · ${eligible.length} match filters`;
 $('assignment-progress').title=state.assignments?.snapshot||'No assignments for this run';
 const host=$('gallery');host.innerHTML='';
 if(!chips.length){host.innerHTML='<div class="empty">No chips match these filters.</div>';reviewLoaded();return}
 const slots=chips.map(chip=>{const slot=document.createElement('div');slot.className='chip-loading-slot';slot.textContent=`${chip.chip_id} · Loading overlay…`;host.append(slot);return slot});
 let cursor=0;
 let loadedCards=0;
 reviewLoading(`Loading chip overlays · 0 / ${chips.length}`);
 async function worker(){while(cursor<chips.length&&generation===galleryGeneration){const index=cursor++,chip=chips[index],slot=slots[index];
  try{const html=await card(chip,signal);if(generation!==galleryGeneration)return;slot.innerHTML=html;const image=slot.querySelector('[data-chip]'),a=assignments[chip.chip_id];
   if(a){const tag=document.createElement('span');tag.className='assignment-tag';tag.textContent=a.queue;tag.title=a.reason;slot.querySelector('.card-head').append(tag)}
   image.addEventListener('click',()=>openModal(chipCache.get(chip.chip_id)));bindTooltips(slot);
  }catch(error){if(generation===galleryGeneration)slot.textContent=`${chip.chip_id} · Could not load overlay; change a filter to retry`}
  if(generation===galleryGeneration)$('review-load-text').textContent=`Loading chip overlays · ${++loadedCards} / ${chips.length}`;
 }}
 await Promise.all(Array.from({length:Math.min(4,chips.length)},worker));
 if(generation===galleryGeneration)reviewLoaded();
}
async function refresh(){showMetrics();await gallery();const chipId=$('modal').dataset.chip;if(!$('modal').hidden&&chipId)openModal(chipCache.get(chipId))}
function addSupervisionLegend(){
 const option=document.querySelector('#sort option[value="false_positive"]');
 if(option)option.textContent='Most non-ignored false positives';
 const worst=document.querySelector('#sort option[value="worst"]');
 if(worst)worst.textContent='Worst adjusted F1';
 $('sort').title='Uses frozen labels and supervision masks at the selected confidence/radius. Ignored unmatched predictions do not count as false positives or matches. Missing legacy masks are counted conservatively. Live edits require a new evaluation.';
 const legend=document.querySelector('.legend'),falsePositive=[...legend.querySelectorAll('.legend-item')].find(item=>item.textContent.includes('False positive'));
 if(falsePositive){falsePositive.lastChild.textContent='Unmatched prediction';falsePositive.title='No unused inventory tree is inside the selected radius; this is not necessarily a non-tree'}
 legend.insertAdjacentHTML('beforeend','<span class="legend-item" title="A high center score here contributes negative center loss"><i class="dot" style="background:#e850e8;outline:2px solid #ff4055"></i>Penalized</span><span class="legend-item" title="The center-loss mask is zero here, usually because this is unlabeled vegetation"><i class="dot" style="background:#e850e8;outline:2px solid #35e5ee"></i>Ignored</span><span class="legend-item" title="Negative center loss is reduced near a labeled tree center"><i class="dot" style="background:#e850e8;outline:2px dashed #ffd166"></i>Reduced penalty</span>')
}
addSupervisionLegend();
async function init(){const [response,statusResponse]=await Promise.all([fetch(withRun('/api/model/summary')),fetch(withRun('/api/curation-status'))]);if(!response.ok){$('gallery').innerHTML='<div class="empty">No validation artifacts loaded.</div>';return}state=await response.json();if(state.no_imagery?.length){const missing=new Set(state.no_imagery);state.chips=state.chips.filter(c=>!missing.has(c.chip_id));if(missing.has(pageParams.get('chip'))){$('gallery').textContent='This tile is outside the source imagery.';return}}if(state.has_additional_validation_chips&&(!pageParams.has('chip')||!state.chips.some(c=>c.chip_id===pageParams.get('chip'))))return initAllReview();$('curate-chip').hidden=!state.curation_available;if(state.curation_available&&statusResponse.ok)curationStatus=(await statusResponse.json()).chips||{};document.title=`${state.run_id} · Model validation`;$('run-detail').textContent=`${state.city} · ${state.cohort} · ${state.training_run_id} · ${state.metrics.ground_truth_trees} truth trees · ${state.metrics.predictions_above_threshold} predictions at the saved threshold · ${state.metrics.chips} chips`;$('review-status').disabled=!state.curation_available;applyReviewFilterDefaults(state);reviewFiltersChanged=refresh;showMetrics();chart();await gallery();const requestedChip=pageParams.get('chip');if(requestedChip&&matchesReviewStatus(curationStatus[requestedChip])){let data=chipCache.get(requestedChip);if(!data){const chipResponse=await fetch(withRun(`/api/model/chip/${requestedChip}`));if(chipResponse.ok){data=await chipResponse.json();chipCache.set(requestedChip,data)}}if(data)openModal(data)}$('modal-close').addEventListener('click',closeModal);$('modal').addEventListener('click',event=>{if(event.target===$('modal'))closeModal()});document.addEventListener('keydown',event=>{if(event.key==='Escape')closeModal()})}init();
</script></body></html>"""
