"""Render the frozen-label NYC adaptation comparison, without touching reviewers."""
import argparse
import json
from pathlib import Path

import pandas as pd
from audit_nyc_adaptation import RUN,BASE,PARENT,digest
from urban_tree_ml.crown_center_score import crown_matches
from urban_tree_ml.sweep_history import policy_truth

def report(root,output):
    audit=json.loads((root/'benchmarks/nyc-native30-v8-audit/verification.json').read_text())
    folders=['nyc-native30-v7-baseline-confidence-sweep','swin-30cm-v6-vs-v7-confidence-sweep',
             'nyc-native30-v8-adaptation-confidence-sweep']
    best=[];sweep=[]
    for folder in folders:
        directory=root/'benchmarks'/folder
        rows=json.loads((directory/'best.json').read_text())
        rows=[r for r in rows if r['run'] in (BASE,PARENT,RUN)]
        provenance=json.loads((directory/'provenance.json').read_text())
        for key,value in provenance.items():
            if any(('/'+r+'/') in key.replace('\\','/') for r in (BASE,PARENT,RUN)):
                assert digest(Path(key))==value,'Stale sweep input: '+key
        best.extend(rows)
        sweep.extend(r for r in json.loads((directory/'sweep.json').read_text()) if r['run'] in (BASE,PARENT,RUN))
    pct=lambda v:f'{v:.2%}'
    names={'usnyc':'NYC','ussfo':'SF','usbos':'Boston'}
    policies=['crown-half-radius-2-4m','fixed-2m','fixed-4m']
    lines=['# NYC native30 v8 adaptation results','',
        'Completed 2026-10-05. NYC-only inventory adaptation of v7; this is not a joint three-city retrain. '
        'All comparisons below use exactly identical frozen labels and chip IDs within each city. '
        'No live annotations were changed and sealed test was not evaluated.','',
        '## Verification','',
        '- Lambda API confirmed exact instance `4b7087f26f564d8588fa1c1459a6746a` terminated on 2026-10-05 after collection; no recovery compute was allocated.',
        f"- Supervisor exit 0; early stopping after epoch 65 (zero-indexed), best checkpoint `{Path(audit['training']['best_checkpoint']).name}`.",
        f"- Best checkpoint SHA256: `{audit['checkpoint_sha256']}`.",
        f"- Downloaded archive SHA256: `{audit['archive_sha256']}`. Collector verified the remote digest before sending the teardown ACK.",
        '- Training: 245 NYC chips / 10,136 retained target rows; validation: 19 chips / 628 rows; 93 test windows sealed and not materialized.',
        '- Evaluation coverage verified: NYC train 245, NYC validation 19, Central Park 187, SF validation 567, Boston validation 637. All five cohorts used the best checkpoint.',
        '- Native August 2023 30 cm NYC orthophoto, 512 px chips; full-chip 64 m guards within 768 m spatial blocks. No Central Park pixels in street training chips.',
        '- Parent vocabulary unchanged: 97 species / 91 genera. Fresh optimizer, learning rate 3e-5, batch 2 / accumulation 16; inherited center/crown objectives.',
        '- Species vocabulary covers 8,585/10,136 retained training targets (84.70%) and 525/628 validation targets (83.60%). Unsupported species remain detection/genus supervision, not invented species classes.','',
        '## Best independently swept goal F2','',
        'Thresholds are selected independently on validation, not test. Goal scoring ignores unmatched predictions in frozen zero-loss-mask regions; it does not credit them as correct. '
        'Crown-aware tolerance uses half the labeled crown radius, clipped to 2–4 m. Species/top-5 accuracy is conditional on matched, species-supported targets at each winning threshold; matched subsets differ.','',
        '| City | Policy | Model | Confidence | F2 | Precision | Recall | Species accuracy | Top-5 | Species matches |',
        '|---|---|---|---:|---:|---:|---:|---:|---:|---:|']
    for city in ['usnyc','ussfo','usbos']:
        for policy in policies:
            for run in [BASE if city=='usnyc' else PARENT,RUN]:
                row=next(r for r in best if r['city']==city and r['policy']==policy and r['run']==run)
                a=row['attributes_on_matched_detections']['species']
                lines.append(f"| {names[city]} | {policy} | {'v8' if run==RUN else 'v7'} | {row['threshold']:.2f} | {pct(row['f2'])} | {pct(row['precision'])} | {pct(row['recall'])} | {pct(a['accuracy'])} | {pct(a['top_k_accuracy'])} | {a['samples']} |")
    lines+=['','## Fixed-confidence controls','','Each cell is v7 → v8; same frozen labels, same matching policy, same confidence.','',
        '| City | Policy | Confidence | F2 | Precision | Recall |','|---|---|---:|---:|---:|---:|']
    for city in ['usnyc','ussfo','usbos']:
        for policy in policies:
            for threshold in [.13,.35]:
                pair=[next(r for r in sweep if r['city']==city and r['policy']==policy and r['run']==run and abs(r['threshold']-threshold)<1e-9)
                      for run in [BASE if city=='usnyc' else PARENT,RUN]]
                cells=[pct(pair[0][key])+' → '+pct(pair[1][key]) for key in ['f2','precision','recall']]
                lines.append(f"| {names[city]} | {policy} | {threshold:.2f} | "+' | '.join(cells)+' |')
    lines+=['','## Same-tree NYC species control','','At confidence 0.13, score only supported inventory targets matched by both models, reducing the changing-match-subset confound. These remain inventory labels, not manually confirmed canopy species.','',
        '| Policy | Common matched targets | v7 correct | v8 correct | v7 accuracy | v8 accuracy |','|---|---:|---:|---:|---:|---:|']
    common=[]
    for policy in policies:
        mapped=[]
        for run in [BASE,RUN]:
            directory=root/'runs'/run/'evaluation/validation-usnyc'
            truth=pd.read_parquet(directory/'ground-truth.parquet').reset_index(drop=True)
            p=pd.read_parquet(directory/'predictions.parquet');p=p[p.score>=.13]
            matches=crown_matches(p,policy_truth(truth,policy),.6)
            mapped.append({(truth.loc[t,'chip_id'],truth.loc[t,'tree_id']):bool(p.loc[i,'species_id']==truth.loc[t,'species_id'])
                for i,t,_ in matches if pd.notna(truth.loc[t,'species_id']) and truth.loc[t,'species_id']>=0})
        keys=mapped[0].keys() & mapped[1].keys();n=len(keys)
        correct=[sum(m[k] for k in keys) for m in mapped]
        common.append(dict(policy=policy,common=n,baseline_correct=correct[0],candidate_correct=correct[1]))
        lines.append(f'| {policy} | {n} | {correct[0]} | {correct[1]} | {pct(correct[0]/n)} | {pct(correct[1]/n)} |')
    lines+=['','## Central Park: unscored prediction changes','','No Central Park ground truth exists. Prediction counts/distributions below are not accuracy or improvement measurements.','',
        '| Model | Predictions ≥0.13 | Predictions ≥0.35 | Top predicted species ≥0.13 |','|---|---:|---:|---|']
    for run in [BASE,RUN]:
        p=pd.read_parquet(root/'runs'/run/'evaluation/train-usnyc-central-park/predictions.parquet')
        taxonomy=json.loads((root/'runs'/run/'evaluation/train-usnyc-central-park/taxonomy.json').read_text())
        counts=p[p.score>=.13].species_id.value_counts().head(5)
        top=', '.join(f"{taxonomy['species'][int(k)]}: {v:,}" for k,v in counts.items())
        lines.append(f"| {'v8' if run==RUN else 'v7'} | {int((p.score>=.13).sum()):,} | {int((p.score>=.35).sum()):,} | {top} |")
    lines+=['','## Interpretation and next step','',
        'The NYC-only adaptation improves NYC street validation, but substantially degrades SF/Boston species recognition. '
        'Treat v8 as an NYC candidate, not a replacement for the shared three-city model. Retain v7 for SF/Boston. '
        'A subsequent jointly balanced fine-tune with SF/Boston replay is a sensible experiment, not something launched by this report.',
        'The NYC evaluation has only 19 spatially selected validation chips, so avoid citywide generalization claims. '
        'Higher confidence is not evidence of better calibration. This is the aggregate effect of additional NYC inventory supervision and optimization; '
        'there is no separate control establishing seasonality or any individual training change as the cause.',
        'No reviewer restart, live curation edit, test evaluation, or automatic model promotion was performed during scoring. '
        'The sweep-history loader currently whitelists SF/Boston: NYC best-sweep display still needs a separate UI integration change; report metrics are complete.',
        '', 'Evidence: `artifacts/benchmarks/nyc-native30-v8-audit/verification.json`, '
        '`nyc-native30-v7-baseline-confidence-sweep`, `nyc-native30-v8-adaptation-confidence-sweep`, '
        'and `swin-30cm-v6-vs-v7-confidence-sweep`.','']
    output.parent.mkdir(parents=True,exist_ok=True);output.write_text('\n'.join(lines),encoding='utf-8')
    (root/'benchmarks/nyc-native30-v8-audit/common-species.json').write_text(json.dumps(common,indent=2)+'\n')
    print(json.dumps(common,indent=2));print(output)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();report(a.root,a.output)
