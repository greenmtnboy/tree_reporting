"""Counterfactual deterministic equal-score NMS on cached GPU parent predictions.

Diagnostic only: does not modify the decoder, cached runs, or live reviewer.
Only resolves equal scores within the EXISTING 5x5 NMS neighborhood.
"""
import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from diagnose_taxonomy_loss import sha
from score_confidence_sweep import sweep, CITY_COHORTS
from score_single_tree_occupancy import summarize
from urban_tree_ml.sweep_history import POLICIES


def break_ties(frame):
    kept=[]
    for _,rows in frame.groupby('chip_id',sort=False):
        blocked=np.full((256,256),-1.0)
        for row in rows.sort_values(['score','output_y','output_x'],ascending=[False,True,True]).itertuples():
            y,x=int(row.output_y),int(row.output_x)
            if blocked[y,x] == row.score:
                continue
            kept.append(row.Index)
            blocked[max(0,y-2):min(256,y+3),max(0,x-2):min(256,x+3)]=row.score
    return frame.loc[kept].copy()


def main(root,output):
    output.mkdir(parents=True,exist_ok=False)
    benchmark=root/'benchmarks/v15-vs-v16-frozen-validation-confidence-sweep'
    run='sf-boston-nyc-naip-swin-30cm-v16-curated-finetune'
    rebuilt=root/'benchmarks/v15-vs-v16-single-tree-occupancy'
    allmetrics=[];occupancy=[];hashes={};cap=[]
    for city,cohort in CITY_COHORTS.items():
        directory=benchmark/'runs'/run/'evaluation'/cohort
        p=pd.read_parquet(directory/'predictions.parquet').reset_index(drop=True)
        t=pd.read_parquet(directory/'ground-truth.parquet')
        hashes[str(directory/'predictions.parquet')]=sha(directory/'predictions.parquet')
        hashes[str(directory/'ground-truth.parquet')]=sha(directory/'ground-truth.parquet')
        adjusted=break_ties(p)
        adjusted.to_parquet(output/(city+'-tie-suppressed.parquet'),index=False)
        # If all retained top512 floors are below the reporting thresholds,
        # missing lower peaks cannot refill any counts at those thresholds.
        floors=p.groupby('chip_id').score.min()
        cap.append(dict(city=city,max_cached_floor=float(floors.max()),
                        floors_at_or_above_013=int(floors.ge(.13).sum())))
        groupdir=rebuilt/'chips'/(city+'-groups')
        manifests=pd.read_parquet(groupdir/'chips.parquet');maps={}
        for row in manifests.itertuples():
            with np.load(groupdir/row.path) as z:
                g=z['single_tree_groups'].squeeze();ids=np.unique(g[g>0])
                if len(ids): maps[row.chip_id]=(g.copy(),ids)
        for label,frame in [('original_gpu',p),('equal_ties_suppressed',adjusted)]:
            for policy in POLICIES:
                allmetrics.extend(dict(city=city,mode=label,policy=policy,**r) for r in sweep(frame,t,.6,policy))
            for threshold in (.13,.35):
                counts=[]
                for chip,(g,ids) in maps.items():
                    selected=frame[frame.chip_id.eq(chip)&frame.score.ge(threshold)]
                    assigned=g[selected.output_y.to_numpy(dtype=int),selected.output_x.to_numpy(dtype=int)]
                    counts.extend(int((assigned==i).sum()) for i in ids)
                occupancy.append(dict(city=city,mode=label,threshold=threshold,**summarize(counts)))
        print(city,'done',flush=True)
    for name,value in [('sweep',allmetrics),('occupancy',occupancy),('cap-check',cap),('provenance',hashes)]:
        (output/(name+'.json')).write_text(json.dumps(value,indent=2))
    (output/'COMPLETE').write_text('Counterfactual equal-score tie suppression only; original artifacts unchanged.\n')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();main(a.root,a.output)
