"""Read-only comparison of paired sampled detection and explicit-region occupancy."""
import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from report_taxonomy_loss import table
from urban_tree_ml.crown_center_score import crown_matches
from urban_tree_ml.goal_metrics import detection


def report(root, directory, destination):
    assert (directory/'COMPLETE').exists()
    metrics=json.loads((directory/'sweep.json').read_text())
    best=json.loads((directory/'best.json').read_text())
    occupancy=json.loads((directory/'occupancy.json').read_text())
    predictions=pd.read_parquet(directory/'predictions.parquet')
    windows=pd.read_parquet(directory/'windows.parquet')
    primary=windows[windows.primary]
    human=[]; large=[]; strata=[]
    for city,cohort in [('ussfo','validation'),('usbos','validation-usbos'),('usnyc','validation-usnyc')]:
        ids=set(primary[primary.city.eq(city)].chip_id)
        truth=pd.read_parquet(root/'runs/sf-boston-nyc-naip-swin-30cm-v16-curated-finetune/evaluation'/cohort/'ground-truth.parquet')
        truth=truth[truth.chip_id.isin(ids)].reset_index(drop=True)
        old=pd.read_parquet(root/'runs/sf-boston-nyc-naip-swin-30cm-v15-curated-finetune/evaluation'/cohort/'ground-truth.parquet')
        old_keys=set(zip(old.chip_id,old.tree_id))
        added_ids={i for i,r in truth.iterrows() if (r.chip_id,r.tree_id) not in old_keys}
        unknown_ids=set(truth.index[truth.genus_id.isna() & truth.species_id.isna()])
        human_ids=set(truth.index[truth.crown_source.eq('human') & truth.crown_radius_m.gt(0)])
        large_ids=set(truth.index[truth.crown_source.eq('human') & truth.crown_radius_m.ge(5)])
        errors={}
        for model in ('parent','best','final'):
            p=predictions[predictions.city.eq(city)&predictions.model.eq(model)&predictions.chip_id.isin(ids)&predictions.score.ge(.13)].reset_index(drop=True)
            matches=crown_matches(p,truth,.6)
            errors[model]={ti:float(p.loc[pi,'crown_radius_m']-truth.loc[ti,'crown_radius_m']) for pi,ti,_ in matches if ti in human_ids}
            matched={ti for _,ti,_ in matches}
            for name,target_ids in [('newly_added_unknown',added_ids & unknown_ids),
                                    ('retained_unknown',unknown_ids-added_ids),
                                    ('known_genus_or_species',set(truth.index)-unknown_ids)]:
                strata.append(dict(city=city,model=model,threshold=.13,stratum=name,
                    targets=len(target_ids),detected=len(target_ids&matched),
                    recall=len(target_ids&matched)/len(target_ids) if target_ids else None))
            large.append(dict(city=city,model=model,threshold=.13,human_large_targets=len(large_ids),
                              detected=len(large_ids&matched),recall=len(large_ids&matched)/len(large_ids) if large_ids else None))
        common=set.intersection(*(set(v) for v in errors.values()))
        for model,e in errors.items():
            a=np.array(list(e.values()));c=np.array([e[i] for i in sorted(common)])
            human.append(dict(city=city,model=model,threshold=.13,matched_human_crowns=len(a),
                              radius_mae_m=float(np.abs(a).mean()) if len(a) else None,
                              common_matched_human_crowns=len(c),
                              common_radius_mae_m=float(np.abs(c).mean()) if len(c) else None,
                              common_radius_bias_m=float(c.mean()) if len(c) else None))
    pooled=[]
    for threshold in (.13,.35):
        for model in ('parent','best','final'):
            rs=[r for r in metrics if r['city'] in ('ussfo','usbos') and r['model']==model and r['policy']=='crown-half-radius-2-4m' and r['threshold']==threshold]
            summed={k:sum(r[k] for r in rs) for k in ('true_positive','false_positive','false_negative','ignored_unmatched')}
            pooled.append(dict(city='SF+Boston',model=model,threshold=threshold,**detection(*[summed[k] for k in ('true_positive','false_positive','false_negative','ignored_unmatched')])))
    summary=dict(pooled=pooled,human_crowns=human,large_human_crown_recall=large,target_strata=strata)
    (directory/'summary.json').write_text(json.dumps(summary,indent=2))
    text='''# v16 trained-checkpoint detection and duplication diagnosis

Paired local CPU float32 inference: parent, best-trained step848 and final step1132. No training or cloud allocation. Detection uses the same155 frozen validation windows as the taxonomy diagnosis (SF66/Boston70/NYC19); five extra windows complete the explicit-single-tree audit of all21 groups on8validationchips. Detection tables exclude those extra windows, avoiding further enrichment of the detection sample.

This is an enriched diagnostic sample, not the full 1,223-window CUDA-bf16 evaluation. Checkpoints use identical pixels, frozen labels/masks, normalization, NMS kernel5 and maximum512detections/chip. Both sides are inferred in CPU fp32, rather than comparing CPU candidates with cached GPU parent outputs. NYC is shown separately: only6curated training chips give it very little direct exposure. Shared-model transfer can still improve or worsen it.

The original total-loss gate remains unchanged and v15 remains the selected parent. Lower taxonomy accuracy is a potential user tradeoff, not automatic permission to promote a candidate. No reviewer or live annotation changes were made.

## Crown-aware detection at fixed confidence

One-to-one center matching, half target crown radius clipped to2–4m. Unmatched zero-supervision predictions are ignored. Metrics are class-agnostic: genus/species mistakes do not count as localization errors.

'''
    fixed=[r for r in metrics if r['policy']=='crown-half-radius-2-4m' and r['threshold'] in (.13,.35)]
    text+=table(['City','Model','Confidence','TP','FP','FN','P %','R %','F1 %','F2 %'],
       [[r['city'],r['model'],r['threshold'],*[r[k] for k in ('true_positive','false_positive','false_negative')],*[f'{100*r[k]:.2f}' for k in ('precision','recall','f1','f2')]] for r in fixed+pooled])
    text+='\n\n## Best-F2 sample confidence\n\nExploratory thresholds optimized on this small sample; these do not replace the full-validation confidence recommendation.\n\n'
    text+=table(['City','Model','Policy','Confidence','P %','R %','F1 %','F2 %'],
       [[r['city'],r['model'],r['policy'],r['threshold'],*[f'{100*r[k]:.2f}' for k in ('precision','recall','f1','f2')]] for r in best])
    text+='\n\n## All explicit single-tree validation groups\n\nOccupancy allows one decoded peak per retained-center ownership group. Excess peaks sum max(count−1,0); covered groups have at least one. This is not distance-matched detection P/R or duplicate-classifier AP. Group counts are per-chip partitions, not unique drawn regions.\n\n'
    text+=table(['City','Model','Confidence','Groups','Detections','Covered','Duplicated groups','Excess peaks'],
       [[r['city'],r['model'],r['threshold'],r['groups'],r['detections'],r['covered'],r['duplicated_groups'],r['excess_peaks']] for r in occupancy if r['threshold'] in (.13,.35)])
    text+='\n\n## Human crown size and large-crown recall\n\nConfidence0.13. Human crown radius errors exclude allometric estimates. Common-match MAE compares exactly the same ground-truth crowns detected by all three checkpoints; it still requires localization within the matching tolerance. Large human crowns are defined here as radius≥5m (diameter≥10m).\n\n'
    def number(v):return '—' if v is None else f'{v:.4f}'
    text+=table(['City','Model','Human matched','Radius MAE m','Common human matched','Common radius MAE m','Common radius bias m'],
       [[r['city'],r['model'],r['matched_human_crowns'],number(r['radius_mae_m']),r['common_matched_human_crowns'],number(r['common_radius_mae_m']),number(r['common_radius_bias_m'])] for r in human])
    text+='\n\n'+table(['City','Model','Large human crowns','Detected','Recall %'],
       [[r['city'],r['model'],r['human_large_targets'],r['detected'],number(None if r['recall'] is None else 100*r['recall'])] for r in large])
    text+='\n\n## Newly added unknown trees\n\nConfidence0.13; matches are made against ALL sampled truth before assigning these target strata. No other targets are removed to make this comparison. Newly added is relative to native v15 frozen labels.\n\n'
    text+=table(['City','Model','Stratum','Targets','Detected','Recall %'],
       [[r['city'],r['model'],r['stratum'],r['targets'],r['detected'],number(None if r['recall'] is None else 100*r['recall'])] for r in strata])
    text+='\n\nArtifacts: predictions, window identities, full confidence sweeps, occupancy at0.12/0.13/0.14/0.35, human-crown diagnostics, input/checkpoint hashes and COMPLETE are retained in `artifacts/benchmarks/v16-trained-detection-cpu-sample`. These results characterize the candidate tradeoff; they do not isolate which curation edits or optimization choices caused it.\n'
    destination.write_text(text,encoding='utf-8')
    print(json.dumps(summary,indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,required=True)
    p.add_argument('--directory',type=Path,required=True)
    p.add_argument('--report',type=Path,required=True)
    a=p.parse_args();report(a.root,a.directory,a.report)
