"""Small, bounded ranking cache over immutable saved inference, not live labels."""
from collections import OrderedDict
from threading import RLock
import math

_lock = RLock()


def validate_settings(threshold, radius):
    threshold, radius = float(threshold), float(radius)
    if not math.isfinite(threshold) or not 0 <= threshold <= 1:
        raise ValueError('threshold must be between zero and one')
    if not math.isfinite(radius) or not 0 < radius <= 100:
        raise ValueError('radius must be positive and at most 100 metres')
    return threshold, radius


def ranking(bundle, threshold, radius):
    threshold, radius = validate_settings(threshold, radius)
    # Serialize first calculations so concurrent gallery requests cannot duplicate
    # work. The cache belongs to the bundle and expires when artifacts reload.
    with _lock:
        if not hasattr(bundle, '_review_rank_cache'):
            bundle._review_rank_cache = OrderedDict()
        cache = bundle._review_rank_cache
        key = (threshold, radius)
        if key not in cache:
            cache[key] = calculate(bundle, threshold, radius)
            if len(cache) > 8:
                cache.popitem(last=False)
            if hasattr(bundle, '_cache_changed'):
                bundle._cache_changed('_review_rank_cache')
        cache.move_to_end(key)
        return cache[key]


def calculate(bundle, threshold, radius):
    import numpy as np
    import polars as pl
    from urban_tree_ml.review_tables import above_threshold, by_chip

    predictions = pl.DataFrame(bundle.predictions)
    truth = pl.DataFrame(bundle.ground_truth)

    provisional = bool(getattr(bundle, 'provisional_reference', None))
    if getattr(bundle, 'metrics', {}).get('prediction_only') and not provisional:
        counts = dict(predictions.filter(above_threshold(threshold)).group_by('chip_id').len().iter_rows())
        return [dict(chip_id=chip, predictions=int(counts.get(chip, 0)), prediction_only=True,
                     ground_truth=None, matched=None, missed=None, false_positive=None,
                     ignored_predictions=None, unmatched_predictions=None, species_errors=None,
                     error_score=None, detection_f1=None) for chip in bundle.chip_ids]
    scale = bundle.config.imagery.resolution_m * bundle.config.targets.output_stride
    if not hasattr(bundle, '_review_rank_arrays'):
        columns = ['output_x', 'output_y', 'species_id']
        # Frozen per-prediction supervision, never live curation. Unknown legacy
        # masks remain counted conservatively rather than silently ignored.
        if 'detection_mask_value' not in predictions:
            predictions = predictions.with_columns(pl.lit(float('nan')).alias('detection_mask_value'))
        ordered = predictions.with_columns(pl.col('score').fill_nan(None)).sort('score', descending=True, maintain_order=True, nulls_last=True)
        groups = {chip: frame.select(*columns, 'score', 'detection_mask_value').to_numpy().astype(float)
                  for chip, frame in by_chip(ordered).items()}
        truths = {chip: frame.select(columns).to_numpy().astype(float)
                  for chip, frame in by_chip(truth).items()}
        bundle._review_rank_arrays = groups, truths
        if hasattr(bundle, '_cache_changed'):
            bundle._cache_changed('_review_rank_arrays')
    groups, truths = bundle._review_rank_arrays
    empty_predictions, empty_truth = np.empty((0, 5)), np.empty((0, 3))
    rows = []
    for chip in bundle.chip_ids:
        ps = groups.get(chip, empty_predictions)
        ps = ps[:np.searchsorted(-ps[:, 3], -threshold, side='right')]
        ts = truths.get(chip, empty_truth)
        xy, species = ts[:, :2], ts[:, 2]
        used = np.zeros(len(ts), dtype=bool)
        matched = wrong = ignored = 0
        for px, py, predicted_species, _, mask in ps:
            distances = np.hypot(xy[:, 0] - px, xy[:, 1] - py)
            eligible = np.flatnonzero(~used & (distances <= radius / scale))
            if not len(eligible):
                ignored += int(np.isfinite(mask) and mask <= 0)
                continue
            i = eligible[np.argmin(distances[eligible])]
            used[i] = True
            matched += 1
            if not np.isnan(species[i]) and species[i] >= 0 and not np.isnan(predicted_species):
                wrong += int(species[i] != predicted_species)
        missed, extra = len(ts) - matched, len(ps) - matched - ignored
        denominator = 2 * matched + extra + missed
        rows.append(dict(chip_id=chip, ground_truth=len(ts), predictions=len(ps),
                         matched=matched, missed=missed, false_positive=extra,
                         ignored_predictions=ignored, unmatched_predictions=len(ps)-matched,
                         species_errors=wrong, error_score=missed + extra + wrong,
                         detection_f1=2 * matched / denominator if denominator else 1.0))
    if provisional:
        from urban_tree_ml.peer_references import provisional_rows
        return provisional_rows(rows)
    return rows
