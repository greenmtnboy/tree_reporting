"""Cached progress metrics from frozen prediction masks; never read live curation."""
from copy import deepcopy
from functools import lru_cache
import json
from pathlib import Path

import polars as pl
from urban_tree_ml.review_tables import above_threshold, scan_predictions


def detection(tp, fp, fn, ignored=0):
    return dict(true_positive=tp, false_positive=fp, false_negative=fn,
                ignored_unmatched=ignored,
                precision=tp/(tp+fp) if tp+fp else 0,
                recall=tp/(tp+fn) if tp+fn else None,
                f1=2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else 0,
                f2=5*tp/(5*tp+fp+4*fn) if 5*tp+fp+4*fn else 0,
                average_precision=None)


def ignored_ids(predictions, matched_ids):
    """Missing/nonfinite supervision means unavailable, not an inventory fallback."""
    if 'detection_mask_value' not in predictions:
        return None
    if isinstance(predictions, pl.DataFrame):
        frame = predictions if 'prediction_id' in predictions else predictions.with_row_index('prediction_id')
    else:
        # Offline callers may still pass a frame with an explicit saved index.
        frame = pl.DataFrame({'prediction_id': predictions.index.to_numpy(),
                              'detection_mask_value': predictions['detection_mask_value'].to_numpy()})
    mask = pl.col('detection_mask_value')
    if not frame.select(mask.is_finite().fill_null(False).all()).item():
        return None
    return set(frame.filter((mask <= 0) & ~pl.col('prediction_id').is_in(matched_ids))['prediction_id'])


def goal_metrics(directory):
    directory = Path(directory)
    paths = [directory/name for name in ('metrics.json','predictions.parquet','matches.parquet')]
    if not all(p.exists() for p in paths):
        return None
    version = tuple((p.stat().st_mtime_ns, p.stat().st_size) for p in paths)
    return deepcopy(_cached(str(directory), version))


@lru_cache(maxsize=256)
def _cached(directory, version):
    directory = Path(directory)
    metrics = json.loads((directory/'metrics.json').read_text())
    if metrics.get('split') == 'test' or metrics.get('prediction_only'):
        return None
    import pyarrow.parquet as pq
    if 'detection_mask_value' not in pq.read_schema(directory/'predictions.parquet').names:
        return None
    predictions = (scan_predictions(directory/'predictions.parquet').filter(above_threshold(metrics['confidence_threshold']))
                   .select('prediction_id', 'detection_mask_value').collect())
    if not predictions.select(pl.col('detection_mask_value').is_finite().fill_null(False).all()).item():
        return None
    matches = pl.read_parquet(directory/'matches.parquet', columns=['radius_m', 'prediction_index'])
    result = {}
    for radius, values in metrics['metrics_by_match_radius_m'].items():
        ids = matches.filter(pl.col('radius_m') == float(radius))['prediction_index']
        matched = pl.col('prediction_id').is_in(ids.cast(pl.Int64).implode())
        tp, ignored = predictions.select(
            matched.sum(), ((pl.col('detection_mask_value') <= 0) & ~matched).sum()).row(0)
        # Bind the calculation to the same threshold and frozen matching export.
        if tp != values['detection']['true_positive']:
            return None
        fn = int(values['detection']['false_negative'])
        result[radius] = {**values, 'detection': detection(tp, len(predictions)-tp-ignored, fn, ignored)}
    return result
