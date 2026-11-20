"""Compute chip rankings for the requested operating point from frozen inference rows."""
import json
from threading import BoundedSemaphore

import polars as pl

from urban_tree_ml.review_ranking import validate_settings
from urban_tree_ml.review_tables import review_backfill_directory

# Bound simultaneous parquet scans and native buffers when several tabs change filters.
_work_slots = BoundedSemaphore(2)
_geometry = ['output_x', 'output_y', 'species_id']


def _coverage(directory):
    path = directory / 'inference-coverage.json'
    if path.exists():
        coverage = json.loads(path.read_text(encoding='utf-8'))
        if coverage.get('complete'):
            return {str(row['chip_id']) for row in coverage['chips']}
    return set()


def _pack(frame, columns):
    # The Rust boundary takes one contiguous little-endian f64 matrix per table.
    return frame.select(pl.col(columns).cast(pl.Float64)).to_numpy(order='c').astype('<f8', copy=False).tobytes()


def rank_evaluation(record, threshold, radius):
    threshold, radius = validate_settings(threshold, radius)
    with _work_slots:
        rows = _calculate(record, threshold, radius)
        extra = review_backfill_directory(record)
        if extra is not None:
            rows.extend(_calculate({**record, 'evaluation_dir': extra}, threshold, radius))
        return rows


def _calculate(record, threshold, radius):
    directory = record['evaluation_dir']
    from urban_tree_ml.peer_references import reference, provisional_rows
    peer = reference(directory)
    # Dictionary-encoded legacy chip IDs can carry a different category order.
    chip_id = pl.col('chip_id').cast(pl.String)
    predictions = pl.scan_parquet(directory / 'predictions.parquet').with_columns(chip_id)
    truth = pl.scan_parquet(peer[0] if peer else directory / 'ground-truth.parquet').with_columns(chip_id)
    score = pl.col('score')
    chips = _coverage(directory)
    if record['metrics'].get('prediction_only') and not peer:
        counts = dict(predictions.group_by('chip_id').agg(
            (score.is_not_nan() & (score >= threshold)).sum().alias('count')
        ).collect().iter_rows())
        chips.update(counts)
        chips.update(truth.select('chip_id').unique().collect().to_series())
        return [dict(chip_id=chip, predictions=counts.get(chip, 0), prediction_only=True,
                     ground_truth=None, matched=None, missed=None, false_positive=None,
                     ignored_predictions=None, unmatched_predictions=None, species_errors=None,
                     error_score=None, detection_f1=None) for chip in sorted(chips)]

    from urban_tree_ml._review_native import match_chips

    mask = 'detection_mask_value'
    if mask not in predictions.collect_schema():
        predictions = predictions.with_columns(pl.lit(float('nan')).alias(mask))
    # Keep chips whose every prediction falls below threshold. Reading the ID
    # column alone avoids materializing their geometry or class outputs.
    chips.update(predictions.select('chip_id').unique().collect().to_series())
    selected = (predictions.filter(score.is_not_nan() & (score.cast(pl.Float64) >= threshold))
                .select('chip_id', 'score', *_geometry, mask)
                .sort(['chip_id', 'score'], descending=[False, True], maintain_order=True)
                .collect())
    trees = truth.select('chip_id', *_geometry).sort('chip_id', maintain_order=True).collect()
    predictions_per_chip = dict(selected.group_by('chip_id').len().iter_rows())
    truth_per_chip = dict(trees.group_by('chip_id').len().iter_rows())
    chips.update(truth_per_chip)
    chip_ids = sorted(chips)
    counts = [(predictions_per_chip.get(chip, 0), truth_per_chip.get(chip, 0)) for chip in chip_ids]
    config = record['bundle_config']
    cell_size = config.imagery.resolution_m * config.targets.output_stride
    matches = match_chips(_pack(selected, [*_geometry, mask]), _pack(trees, _geometry),
                          counts, radius / cell_size)
    rows = []
    for chip, (npred, ntruth), (matched, wrong, ignored) in zip(chip_ids, counts, matches, strict=True):
        missed = ntruth - matched
        extra = npred - matched - ignored
        denominator = 2 * matched + extra + missed
        rows.append(dict(chip_id=chip, ground_truth=ntruth, predictions=npred,
                         matched=matched, missed=missed, false_positive=extra,
                         ignored_predictions=ignored, unmatched_predictions=npred-matched,
                         species_errors=wrong, error_score=missed + extra + wrong,
                         detection_f1=2 * matched / denominator if denominator else 1.0))
    return provisional_rows(rows) if peer else rows
