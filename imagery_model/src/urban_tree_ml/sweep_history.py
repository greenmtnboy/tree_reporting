"""Read verified, precomputed native validation confidence sweeps for history."""
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path


POLICIES = ('crown-half-radius-2-4m', 'fixed-2m', 'fixed-4m')


def policy_truth(truth, policy):
    if policy not in POLICIES:
        raise ValueError(f'Unknown matching policy: {policy}')
    if policy == 'crown-half-radius-2-4m':
        return truth
    truth = truth.copy()
    truth['crown_radius_m'] = 4.0 if policy == 'fixed-2m' else 8.0
    return truth


def swept_attributes(directory, threshold, policy='crown-half-radius-2-4m'):
    """Offline attribute scoring at exactly the swept detection operating point."""
    import pandas as pd
    from urban_tree_ml.crown_center_score import crown_matches
    from urban_tree_ml.evaluation import _attribute_metrics
    directory = Path(directory)
    cfg = json.loads((directory/'evaluation-metadata.json').read_text())['config']
    predictions = pd.read_parquet(directory/'predictions.parquet')
    truth = pd.read_parquet(directory/'ground-truth.parquet').reset_index(drop=True)
    predictions = predictions[predictions.score >= threshold]
    scale = cfg['imagery']['resolution_m'] * cfg['targets']['output_stride']
    return _attribute_metrics(predictions, truth, crown_matches(predictions, policy_truth(truth, policy), scale),
                              dbh_tolerance_in=cfg['evaluation'].get('dbh_tolerance_in', 2.0))


@lru_cache(maxsize=128)
def _hash(path, mtime, size):
    with open(path, 'rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def best_sweeps(root, policy='crown-half-radius-2-4m'):
    """No scoring or live-label reads on requests; stale exports are unavailable."""
    root = Path(root)
    found = {}
    for path in sorted((root/'benchmarks').glob('*confidence-sweep/best.json'),
                       key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            provenance = json.loads((path.parent/'provenance.json').read_text())
            rows = json.loads(path.read_text())
            for row in rows:
                if row.get('policy', 'crown-half-radius-2-4m') != policy:
                    continue
                run, city = row['run'], row['city'].lower()
                if city not in ('ussfo', 'usbos') or Path(run).name != run:
                    continue
                key = (run, city)
                if key in found:
                    continue
                cohort = 'validation' if city == 'ussfo' else 'validation-usbos'
                directory = root/'runs'/run/'evaluation'/cohort
                valid = True
                for name in ('predictions.parquet', 'ground-truth.parquet', 'evaluation-metadata.json'):
                    file = directory/name
                    expected = provenance.get(str(file)) or provenance.get(file.as_posix())
                    stat = file.stat()
                    if not expected or _hash(str(file), stat.st_mtime_ns, stat.st_size) != expected:
                        valid = False
                        break
                if not valid or any(not isinstance(row.get(k), (float, int)) or
                                    not math.isfinite(row[k]) or not 0 <= row[k] <= 1
                                    for k in ('threshold', 'precision', 'recall', 'f1', 'f2')):
                    continue
                found[key] = dict(row, policy=policy,
                                  source=str(path.relative_to(root)))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return found
