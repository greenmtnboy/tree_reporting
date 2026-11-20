"""Sweep native frozen validation predictions; no GPU or live annotations."""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from urban_tree_ml.crown_center_score import tolerance, crown_matches
from urban_tree_ml.goal_metrics import detection

CITY_COHORTS={'ussfo':'validation','usbos':'validation-usbos','usnyc':'validation-usnyc'}

def sweep(predictions, truth, scale, policy='crown-half-radius-2-4m'):
    from urban_tree_ml.sweep_history import policy_truth
    # Diagnostic-only policy: do not route 1 m through crown tolerance,
    # which intentionally clamps to a minimum of 2 m.
    fixed_one_m = policy == 'fixed-1m'
    if not fixed_one_m:
        truth = policy_truth(truth, policy)
    if scale <= 0:
        raise ValueError('Scale must be positive')
    p = predictions.sort_values('score', ascending=False, kind='stable').copy()
    if not np.isfinite(p.detection_mask_value).all():
        raise ValueError('Missing frozen supervision')
    groups = {}
    for chip, rows in truth.groupby('chip_id', sort=False):
        groups[str(chip)] = (rows.output_x.to_numpy(), rows.output_y.to_numpy(),
            np.full(len(rows), 1/scale) if fixed_one_m else
            np.array([tolerance(v)/scale for v in rows.crown_radius_m])
            if 'crown_radius_m' in rows else np.full(len(rows),2/scale),
            np.zeros(len(rows), dtype=bool))
    matched = np.zeros(len(p), dtype=bool)
    for i, row in enumerate(p.itertuples()):
        group = groups.get(str(row.chip_id))
        if group is None:
            continue
        xs, ys, radii, used = group
        distances = np.hypot(xs-row.output_x, ys-row.output_y)
        eligible = (~used) & (distances <= radii)
        if eligible.any():
            index = np.argmin(np.where(eligible, distances, np.inf))
            used[index] = True
            matched[i] = True
    # Confidence-ordered greedy matching has the same assignments for every
    # prefix: lower-confidence detections cannot change earlier assignments.
    scores = p.score.to_numpy()
    ignored = (~matched) & (p.detection_mask_value.to_numpy() <= 0)
    tp = np.r_[0, np.cumsum(matched)]
    ig = np.r_[0, np.cumsum(ignored)]
    rows = []
    for threshold in np.arange(1,96)/100:
        count = int(np.searchsorted(-scores, -threshold, side='right'))
        hits, skipped = int(tp[count]), int(ig[count])
        rows.append({'threshold':float(threshold),
                     **detection(hits,count-hits-skipped,len(truth)-hits,skipped)})
    # Compare the optimized match/count path against the existing reference
    # scorer at the established operating point for every actual cohort.
    if fixed_one_m:
        from urban_tree_ml.evaluation import _greedy_matches
        reference = _greedy_matches(predictions[predictions.score >= .35], truth,
                                    radius_output_px=1/scale)
    else:
        reference = crown_matches(predictions[predictions.score >= .35], truth, scale)
    assert rows[34]['true_positive'] == len(reference)
    return rows


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--runs', nargs='+', default=['sf-boston-naip-swin-v9-finetune','sf-boston-naip-swin-30cm-v1-retry1'])
    parser.add_argument('--cities',nargs='+',choices=CITY_COHORTS,default=['ussfo','usbos'],
                        help='Explicit validation cities; sealed test and prediction-only cohorts are never scored')
    from urban_tree_ml.sweep_history import POLICIES
    parser.add_argument('--policies', nargs='+', choices=(*POLICIES, 'fixed-1m'), default=list(POLICIES))
    args=parser.parse_args()
    if args.output.exists():
        raise ValueError('Output already exists')
    all_rows=[];best=[];hashes={}
    for run in args.runs:
        for city in args.cities:
            cohort=CITY_COHORTS[city]
            directory=args.root/'runs'/run/'evaluation'/cohort
            cfg=json.loads((directory/'evaluation-metadata.json').read_text())['config']
            truth=pd.read_parquet(directory/'ground-truth.parquet').reset_index(drop=True)
            p=pd.read_parquet(directory/'predictions.parquet')
            scale=cfg['imagery']['resolution_m']*cfg['targets']['output_stride']
            for policy in args.policies:
                rows=sweep(p,truth,scale,policy)
                rows=[dict(run=run,city=city,policy=policy,**row) for row in rows]
                winner=max(rows,key=lambda r:(r['f2'],r['threshold']))
                from urban_tree_ml.sweep_history import swept_attributes
                if policy == 'fixed-1m':
                    from urban_tree_ml.evaluation import _attribute_metrics, _greedy_matches
                    selected = p[p.score >= winner['threshold']]
                    matches = _greedy_matches(selected, truth, radius_output_px=1/scale)
                    winner['attributes_on_matched_detections'] = _attribute_metrics(
                        selected, truth, matches,
                        dbh_tolerance_in=cfg['evaluation'].get('dbh_tolerance_in', 2.0))
                else:
                    winner['attributes_on_matched_detections'] = swept_attributes(directory, winner['threshold'], policy)
                best.append(winner);all_rows.extend(rows)
                print(json.dumps(winner),flush=True)
            for name in ['ground-truth.parquet','predictions.parquet','evaluation-metadata.json']:
                path=directory/name;hashes[str(path)]=hashlib.sha256(path.read_bytes()).hexdigest()
    args.output.mkdir(parents=True)
    (args.output/'sweep.json').write_text(json.dumps(all_rows,indent=2),encoding='utf-8')
    (args.output/'best.json').write_text(json.dumps(best,indent=2),encoding='utf-8')
    (args.output/'provenance.json').write_text(json.dumps(hashes,indent=2),encoding='utf-8')


if __name__=='__main__':
    main()
