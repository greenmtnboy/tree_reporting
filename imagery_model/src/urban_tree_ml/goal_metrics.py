"""Cached progress metrics from frozen prediction masks; never read live curation."""
from copy import deepcopy
from functools import lru_cache
import json
from pathlib import Path

import numpy as np
import pandas as pd


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
    if not np.isfinite(predictions.detection_mask_value).all():
        return None
    return set(predictions.index[(predictions.detection_mask_value <= 0)
                                 & ~predictions.index.isin(matched_ids)])


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
    if metrics.get('split') == 'test':
        return None
    import pyarrow.parquet as pq
    if 'detection_mask_value' not in pq.read_schema(directory/'predictions.parquet').names:
        return None
    predictions = pd.read_parquet(directory/'predictions.parquet', columns=['score','detection_mask_value'])
    predictions = predictions[predictions.score >= metrics['confidence_threshold']]
    matches = pd.read_parquet(directory/'matches.parquet')
    result = {}
    for radius, values in metrics['metrics_by_match_radius_m'].items():
        ids = set(matches.loc[matches.radius_m == float(radius), 'prediction_index'].astype(int))
        ignored = ignored_ids(predictions, ids)
        if ignored is None:
            return None
        tp = len(set(predictions.index) & ids)
        # Bind the calculation to the same threshold and frozen matching export.
        if tp != values['detection']['true_positive']:
            return None
        fn = int(values['detection']['false_negative'])
        result[radius] = {**values, 'detection': detection(tp, len(predictions)-tp-len(ignored), fn, len(ignored))}
    return result
