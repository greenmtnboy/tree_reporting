"""Run-native frozen-supervision F2; never reads live curation or runs inference."""
import hashlib
import json
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
from urban_tree_ml.evaluation import _greedy_matches

ROOT=Path('C:/Users/ethan/coding_projects/sf_tree_reporting/imagery_model/artifacts')
RUNS=['sf-boston-naip-curation-v13-finetune','sf-boston-naip-curation-v14-finetune']
out=ROOT/'benchmarks/v14-v13-goal-aligned-native'
out.mkdir(parents=True,exist_ok=False)
rows=[]; hashes={}; sources={}
for run in RUNS:
    for city,cohort in [('ussfo','validation'),('usbos','validation-usbos')]:
        d=ROOT/'runs'/run/'evaluation'/cohort
        for name in ['predictions.parquet','ground-truth.parquet','metrics.json','evaluation-metadata.json']:
            hashes[str(d/name)]=hashlib.sha256((d/name).read_bytes()).hexdigest()
        m=json.loads((d/'evaluation-metadata.json').read_text())
        t=pd.read_parquet(d/'ground-truth.parquet').reset_index(drop=True)
        p=pd.read_parquet(d/'predictions.parquet')
        p=p[p.score>=.35].copy()
        assert np.isfinite(p.detection_mask_value).all() and np.isfinite(p.center_target).all()
        scale=m['config']['imagery']['resolution_m']*m['config']['targets']['output_stride']
        for radius in [2.,4.]:
            matches=_greedy_matches(p,t,radius_output_px=radius/scale)
            ids={x[0] for x in matches}; unmatched=p[~p.index.isin(ids)]
            ignored=unmatched.detection_mask_value<=0
            tp=len(matches);fp=int((~ignored).sum());fn=len(t)-tp
            rows.append(dict(run=run,city=city,radius_m=radius,threshold=.35,tp=tp,fp=fp,fn=fn,
                ignored_unmatched=int(ignored.sum()),f2=5*tp/(5*tp+fp+4*fn),
                precision=tp/(tp+fp) if tp+fp else 0,recall=tp/len(t),
                human_tree_matches=sum(str(t.loc[i,'tree_id']).startswith('manual-') for _,i,_ in matches)))
        cfg=json.loads((ROOT/'runs'/run/f'config-{city}.json').read_text())
        labels=pd.read_parquet(ROOT/'chips'/cfg['dataset']/'labels.parquet')
        sources[run+'-'+city]={f'{split}/{source}':n for (split,source),n in Counter(
            zip(labels['split'],labels.crown_source.fillna('none'))).items()}
        sources[run+'-'+city]['crown_validation_metrics']=json.loads((d/'metrics.json').read_text())['metrics_by_match_radius_m']['4.0']['attributes_on_matched_detections']
(out/'metrics.json').write_text(json.dumps(rows,indent=2))
(out/'provenance.json').write_text(json.dumps(hashes,indent=2))
(out/'crown-audit.json').write_text(json.dumps(sources,indent=2))
lines=['# Goal-aligned F2: v13 vs v14','',
 'Confidence 0.35. Each run uses its own frozen labels and saved supervision values. This is run-native progress, not a common-mask causal decomposition.',
 'Match known targets (including eligible human-added trees) first. Ignore unmatched predictions with detection_mask_value <= 0; they are not true positives. All remaining unmatched predictions count as false positives. Soft center weights are not fractional credit.','',
 '| City | Radius | v13 F2 | v14 F2 | Delta |','|---|---:|---:|---:|---:|']
for city in ['ussfo','usbos']:
 for radius in [2.,4.]:
  a,b=[r for r in rows if r['city']==city and r['radius_m']==radius]
  lines.append(f"| {city} | {radius:g} m | {a['f2']:.1%} | {b['f2']:.1%} | {(b['f2']-a['f2'])*100:+.1f} pp |")
lines+=['','No live annotations changed. Test is not scored. Precision is conditional on supervised regions, not independently verified discovery precision.']
(out/'REPORT.md').write_text('\n'.join(lines))
print('\n'.join(lines))
