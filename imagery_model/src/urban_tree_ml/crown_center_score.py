"""One-to-one target-size matching; no inference and no live annotation reads."""
import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
from urban_tree_ml.evaluation import _greedy_matches
from urban_tree_ml.goal_metrics import detection


def tolerance(radius_m):
    return float(np.clip(.5*radius_m,2.,4.)) if pd.notna(radius_m) and math.isfinite(radius_m) and radius_m>0 else 2.


def crown_matches(predictions, truth, scale):
    if scale <= 0: raise ValueError('Scale must be positive')
    groups = {}
    for chip, frame in truth.groupby('chip_id',sort=False):
        groups[str(chip)] = [(i,float(row.output_x),float(row.output_y),
                             tolerance(row.get('crown_radius_m'))/scale)
                            for i,row in frame.iterrows()]
    used=set(); result=[]
    for i,row in predictions.sort_values('score',ascending=False,kind='stable').iterrows():
        best=None; distance=math.inf
        for tid,x,y,r in groups.get(str(row.chip_id),[]):
            if tid in used: continue
            d=math.hypot(row.output_x-x,row.output_y-y)
            if d<=r and d<distance: best,distance=tid,d
        if best is not None:
            used.add(best);result.append((int(i),int(best),distance))
    return result


def build(root, baseline, candidate, output):
    rows=[];hashes={}
    for city,cohort in [('ussfo','validation'),('usbos','validation-usbos')]:
        baseline_dir=root/'runs'/baseline/'evaluation'/cohort
        truth=pd.read_parquet(baseline_dir/'ground-truth.parquet').reset_index(drop=True)
        cfg=json.loads((baseline_dir/'evaluation-metadata.json').read_text())['config']
        scale=cfg['imagery']['resolution_m']*cfg['targets']['output_stride']
        for run in dict.fromkeys([baseline,candidate]):
            d=root/'runs'/run/'evaluation'/cohort
            current=pd.read_parquet(d/'ground-truth.parquet').reset_index(drop=True)
            if not truth.equals(current):
                raise ValueError('Frozen validation labels differ; controlled comparison invalid')
            p=pd.read_parquet(d/'predictions.parquet');p=p[p.score>=.35]
            assert np.isfinite(p.detection_mask_value).all()
            for policy in ['fixed-2m','fixed-4m','crown-half-radius-2to4m']:
                matches=(crown_matches(p,truth,scale) if policy.startswith('crown') else
                         _greedy_matches(p,truth,radius_output_px=(2 if policy=='fixed-2m' else 4)/scale))
                ids={m[0] for m in matches}; unmatched=p[~p.index.isin(ids)]
                ignored=int((unmatched.detection_mask_value<=0).sum());tp=len(matches)
                rows.append(dict(city=city,run=run,policy=policy,
                                 **detection(tp,len(unmatched)-ignored,len(truth)-tp,ignored)))
            for name in ['ground-truth.parquet','predictions.parquet','evaluation-metadata.json']:
                path=d/name;hashes[str(path)]=hashlib.sha256(path.read_bytes()).hexdigest()
    output.mkdir(parents=True,exist_ok=False)
    (output/'metrics.json').write_text(json.dumps(rows,indent=2))
    (output/'provenance.json').write_text(json.dumps(hashes,indent=2))
    lines=['# Crown-center experiment: goal-aligned scoring','',
           'Confidence 0.35. Identical frozen ground truth verified. Target radius, never predicted radius, sets size-aware tolerance: half radius clipped to 2–4 m; unknown radius uses 2 m. One-to-one, confidence-ordered nearest eligible matching. Unmatched zero-mask predictions are ignored, not credited.','',
           '| City | Model | Matching | F2 | Precision | Recall |','|---|---|---|---:|---:|---:|']
    for r in rows:
        lines.append(f"| {r['city']} | {r['run']} | {r['policy']} | {r['f2']:.2%} | {r['precision']:.2%} | {r['recall']:.2%} |")
    lines += ['', 'Validation center heatmaps and masks are unchanged in this experiment. The training change only reduces negative focal-loss weight near large labeled centers; it does not turn their crowns into extra positive centers. Without a parallel unchanged-target fine-tune, model deltas also include additional optimization time, not just target-width effects. Test remains sealed.']
    (output/'REPORT.md').write_text('\n'.join(lines))
    return rows


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True)
    p.add_argument('--baseline',required=True);p.add_argument('--candidate',required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    print(json.dumps(build(a.root,a.baseline,a.candidate,a.output)))
