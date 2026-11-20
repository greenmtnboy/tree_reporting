"""Record immutable same-model validation-curation intervals, not training gains."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path

import pandas as pd

from count_validation_curation_delta import CITIES, RUN
from urban_tree_ml.goal_metrics import detection
from urban_tree_ml.sweep_history import POLICIES, swept_attributes


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def gain(old, new, n):
    delta = (new-old)*100 if old is not None and new is not None else None
    return {'before':old,'after':new,'delta_pp':delta,
            'pp_per_100_reviewed_trees':delta*100/n if n and delta is not None else None}


def safe_json(value):
    if isinstance(value, dict): return {k:safe_json(v) for k,v in value.items()}
    if isinstance(value, list): return [safe_json(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value): return None
    return value


def make_interval(root, baseline, current, run=RUN, threshold=.13):
    for directory in (baseline,current):
        assert (directory/'COMPLETE').exists(), 'Only completed evaluations can enter history'
    counts=json.loads((current/'curation-counts.json').read_text())
    assert Path(counts['baseline_path']).resolve()==baseline.resolve(), 'Count/score baseline mismatch'
    bp=json.loads((baseline/'provenance.json').read_text())
    cp=json.loads((current/'provenance.json').read_text())
    assert run in bp['runs'] and run in cp['runs']
    curves=[json.loads((d/'sweep.json').read_text()) for d in (baseline,current)]
    bests=[json.loads((d/'best.json').read_text()) for d in (baseline,current)]
    result={'schema_version':1,'created_at':datetime.now(timezone.utc).isoformat(),
            'model':run,'baseline':str(baseline),'current':str(current),
            'snapshot_times':[bp['created_at'],cp['created_at']],
            'threshold':threshold,'primary_policy':'crown-half-radius-2-4m',
            'denominator_policy':counts['count_policy'],
            'interpretation':'Descriptive score change per net tree record edited, not causal per-tree effect. '
                'Mask/region edits and registration also affect scores. Correct curation can lower scores. '
                'Changing matched-species support affects conditional accuracy. One interval cannot establish saturation.',
            'test_evaluated':False,'cities':{},'provenance':{}}
    totals={policy:[Counter(),Counter()] for policy in POLICIES}
    for city,(_,cohort) in CITIES.items():
        directories=[d/'runs'/run/'evaluation'/cohort for d in (baseline,current)]
        predictions=[pd.read_parquet(d/'predictions.parquet') for d in directories]
        # The model's actual outputs must be identical; only target supervision changes.
        pd.testing.assert_frame_equal(*[p.drop(columns=['detection_mask_value','center_target'],errors='ignore')
                                       for p in predictions],check_exact=True)
        chips=[pd.read_parquet(d/'chips'/f'{city}-current'/'chips.parquet') for d in (baseline,current)]
        assert set(chips[0].chip_id)==set(chips[1].chip_id), 'Evaluation cohort changed'
        for directory in directories:
            for filename in ('predictions.parquet','ground-truth.parquet','evaluation-metadata.json'):
                result['provenance'][str(directory/filename)]=sha(directory/filename)
        truth=[pd.read_parquet(d/'ground-truth.parquet') for d in directories]
        row=counts['cities'][city]
        n=row['unique_tree_edits']
        result['cities'][city]=entry={
            'reviewed_trees':n,'tree_edit_categories':row['categories'],
            'region_edits_by_mode':dict(Counter(r['mode'] for r in row['changed_validation_regions'])),
            'changed_validation_scenes':row['changed_validation_scenes'],
            'chips':len(chips[0]),'targets_before':len(truth[0]),'targets_after':len(truth[1]),
            'predictions_with_changed_scoring_mask':int((predictions[0].detection_mask_value !=
                                                        predictions[1].detection_mask_value).sum()),
            'fixed_threshold':{},'best_threshold_reference':{}}
        for policy in POLICIES:
            selected=[next(r for r in rows if r['run']==run and r['city']==city and
                           r['policy']==policy and r['threshold']==threshold) for rows in curves]
            entry['fixed_threshold'][policy]={
                metric:gain(selected[0][metric],selected[1][metric],n) for metric in ('f2','precision','recall')}
            for i,r in enumerate(selected):
                totals[policy][i].update({k:r[k] for k in ('true_positive','false_positive','false_negative','ignored_unmatched')})
            winners=[next(r for r in rows if r['run']==run and r['city']==city and r['policy']==policy) for rows in bests]
            entry['best_threshold_reference'][policy]={
                'thresholds':[r['threshold'] for r in winners],
                'f2':gain(winners[0]['f2'],winners[1]['f2'],n),
                'attributes':[r['attributes_on_matched_detections'] for r in winners]}
        attrs=[swept_attributes(d,threshold,result['primary_policy']) for d in directories]
        entry['fixed_threshold_species']={
            'samples':[a['species']['samples'] if a['species'] else 0 for a in attrs],
            **{metric:gain(attrs[0]['species'].get(metric) if attrs[0]['species'] else None,
                           attrs[1]['species'].get(metric) if attrs[1]['species'] else None,n)
               for metric in ('accuracy','top_k_accuracy')},
            'vegetation_category':[a['vegetation_category'] for a in attrs]}
        print(city,'trees',n,'fixed F2',entry['fixed_threshold'][result['primary_policy']]['f2'],flush=True)
    n=sum(r['reviewed_trees'] for r in result['cities'].values())
    result['total_reviewed_trees']=n
    result['micro_pooled_fixed_threshold']={}
    for policy, sides in totals.items():
        metrics=[detection(s['true_positive'],s['false_positive'],s['false_negative'],s['ignored_unmatched']) for s in sides]
        result['micro_pooled_fixed_threshold'][policy]={k:gain(metrics[0][k],metrics[1][k],n) for k in ('f2','precision','recall')}
    for d in (baseline,current):
        for filename in ('best.json','sweep.json','provenance.json'):
            result['provenance'][str(d/filename)]=sha(d/filename)
    result['provenance'][str(current/'curation-counts.json')]=sha(current/'curation-counts.json')
    # Verify original frozen evaluations are still intact as well.
    for path,expected in cp['files'].items():
        if '/runs/' in path.replace('\\','/'):
            assert sha(path)==expected, 'Original evaluation changed'
    return safe_json(result)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--baseline',type=Path,required=True)
    parser.add_argument('--current',type=Path,required=True)
    parser.add_argument('--run',default=RUN)
    args=parser.parse_args()
    destination=args.current/'curation-progress.json'
    assert not destination.exists(), 'Do not overwrite a tracked interval'
    history=args.root/'benchmarks/validation-curation-progress'
    history.mkdir(exist_ok=True)
    pointer=history/(args.current.name+'.json')
    assert not pointer.exists(), 'Interval already registered'
    result=make_interval(args.root,args.baseline,args.current,args.run)
    destination.write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    pointer.write_text(json.dumps({'interval':str(destination),'sha256':sha(destination),
        'baseline':str(args.baseline),'current':str(args.current),'model':args.run},indent=2)+'\n')
    print('Registered immutable interval:',destination,flush=True)
