"""Summarize paired ground-truth-center taxonomy losses from the CPU diagnostic."""
import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd


def table(headers, rows):
    return '\n'.join(['| '+' | '.join(headers)+' |',
                      '| '+' | '.join(['---']*len(headers))+' |'] +
                     ['| '+' | '.join(map(str, row))+' |' for row in rows])


def report(directory, destination):
    assert (directory/'COMPLETE').exists()
    data = pd.read_parquet(directory/'targets.parquet')
    windows = pd.read_parquet(directory/'windows.parquet')
    batches = pd.read_parquet(directory/'batches.parquet')
    provenance = json.loads((directory/'provenance.json').read_text())
    keys = ['city','chip_id','batch','head','y','x','target','target_name']
    parent = data[data.model.eq('parent')]
    assert not parent.duplicated(keys).any()
    pairs = []
    for candidate in ('best','final'):
        other = data[data.model.eq(candidate)]
        pair = parent.merge(other, on=keys, suffixes=('_parent','_candidate'), validate='one_to_one')
        assert len(pair) == len(parent) == len(other)
        np.testing.assert_array_equal(pair.objective_weight_parent, pair.objective_weight_candidate)
        pair['candidate'] = candidate
        pair['delta'] = pair.loss_candidate - pair.loss_parent
        pair['contribution'] = pair.delta * pair.objective_weight_parent
        pair['transition'] = np.select([
            pair.correct_parent & pair.correct_candidate,
            ~pair.correct_parent & pair.correct_candidate,
            pair.correct_parent & ~pair.correct_candidate],
            ['stayed_correct','fixed','broke'], default='stayed_wrong')
        for head in ('genus','species'):
            b = batches[batches['head'].eq(head)].copy()
            means = b.groupby('model').apply(lambda x: (x.loss*x.samples).sum()/x.samples.sum(), include_groups=False)
            assert abs(pair.loc[pair['head'].eq(head), 'contribution'].sum() -
                       (means[candidate]-means['parent'])) < 2e-6
        pairs.append(pair)
    paired = pd.concat(pairs, ignore_index=True)
    paired.to_parquet(directory/'paired-targets.parquet', index=False)
    overall=[]
    for (model,head), d in data.groupby(['model','head']):
        b=batches[batches.model.eq(model)&batches['head'].eq(head)]
        overall.append(dict(model=model,head=head,n=len(d),target_mean_ce=d.loss.mean(),
                            batch_mean_ce=(b.loss*b.samples).sum()/b.samples.sum(),
                            accuracy=d.correct.mean(),top5=d.top5.mean(),confidence=d.confidence.mean()))
    bycity=paired.groupby(['candidate','head','city']).agg(n=('delta','size'),
        ce_parent=('loss_parent','mean'),ce_candidate=('loss_candidate','mean'),
        accuracy_parent=('correct_parent','mean'),accuracy_candidate=('correct_candidate','mean'),
        delta=('delta','mean'),contribution=('contribution','sum')).reset_index()
    byclass=paired.groupby(['candidate','head','target_name']).agg(n=('delta','size'),
        ce_parent=('loss_parent','mean'),ce_candidate=('loss_candidate','mean'),
        accuracy_parent=('correct_parent','mean'),accuracy_candidate=('correct_candidate','mean'),
        delta=('delta','mean'),contribution=('contribution','sum')).reset_index()
    transitions=paired.groupby(['candidate','head','transition']).agg(n=('delta','size'),
        delta=('delta','mean'),contribution=('contribution','sum'),
        confidence_parent=('confidence_parent','mean'),confidence_candidate=('confidence_candidate','mean')).reset_index()
    chips=paired.groupby(['candidate','head','city','chip_id']).agg(n=('delta','size'),
        delta=('delta','mean'),contribution=('contribution','sum')).reset_index()
    summary={'provenance':provenance,'overall':overall,'cities':bycity.to_dict('records'),
             'classes':byclass.to_dict('records'),'transitions':transitions.to_dict('records')}
    (directory/'summary.json').write_text(json.dumps(summary,indent=2))
    byclass.to_parquet(directory/'classes.parquet',index=False)
    chips.to_parquet(directory/'chip-deltas.parquet',index=False)
    text='# v16 genus/species regression diagnosis\n\n'
    text+='Local CPU float32 evaluation of the retained parent, best-trained step848 and final step1132 at identical frozen validation target centers. This is an **enriched diagnostic sample**, not a full-validation estimate or an exact reproduction of CUDA bf16 selection. No training, test evaluation, cloud allocation or live annotation change.\n\n'
    text+=f"Sample: {len(windows)} windows; "+', '.join(f'{c} {n}' for c,n in windows.city.value_counts().items())+'. Seed20261026 selects32 original batches per SF/Boston, adds all changed-label batches, and includes all NYC validation windows. Original batch partners are preserved. Normalization is from collected v16 city manifests; class mapping is unchanged. Model/backbone/loss code was checked against the frozen source archive.\n\n'
    text+='Loss is evaluated at labeled centers regardless of whether the detector finds them. Accuracy here is therefore ground-truth-center classification accuracy, not the report’s detection-matched accuracy. Unknown targets are excluded. Genus/species have different support. Batch-mean CE reproduces the averaging form of training on this sample; target-mean CE weights every eligible center equally.\n\n'
    text+=table(['Model','Head','Targets','Batch-mean CE','Target-mean CE','Top-1 %','Top-5 %'],
                [[r['model'],r['head'],r['n'],f"{r['batch_mean_ce']:.6f}",f"{r['target_mean_ce']:.6f}",f"{100*r['accuracy']:.2f}",f"{100*r['top5']:.2f}"] for r in overall])
    text+='\n\n## City breakdown\n\nContributions sum to the **sample** batch-mean change, not the full-run loss change.\n\n'
    text+=table(['Candidate','Head','City','Targets','CE change','Top-1 parent %','Top-1 candidate %','Sample loss contribution'],
                [[r.candidate,r.head,r.city,r.n,f'{r.delta:+.6f}',f'{100*r.accuracy_parent:.2f}',f'{100*r.accuracy_candidate:.2f}',f'{r.contribution:+.6f}'] for r in bycity.itertuples()])
    text+='\n\n## Correctness versus confidence\n\n'
    text+=table(['Candidate','Head','Transition','Targets','Mean CE change','Sample contribution','Confidence parent %','Confidence candidate %'],
                [[r.candidate,r.head,r.transition,r.n,f'{r.delta:+.6f}',f'{r.contribution:+.6f}',f'{100*r.confidence_parent:.2f}',f'{100*r.confidence_candidate:.2f}'] for r in transitions.itertuples()])
    for head in ('genus','species'):
        text+=f'\n\n## Largest best-trained {head} loss increases\n\nMinimum20 sample targets; ranked by contribution to sample batch-mean loss. These are exploratory rankings on the sampled windows.\n\n'
        ranked=byclass[byclass.candidate.eq('best') & byclass['head'].eq(head) & byclass.n.ge(20)].sort_values('contribution',ascending=False).head(10)
        text+=table(['Class','Targets','Mean CE change','Top-1 parent %','Top-1 best %','Sample contribution'],
                    [[r.target_name,r.n,f'{r.delta:+.6f}',f'{100*r.accuracy_parent:.2f}',f'{100*r.accuracy_candidate:.2f}',f'{r.contribution:+.6f}'] for r in ranked.itertuples()])
    text+='\n\n## Limits and next checks\n\nThis identifies associations, not whether fresh-optimizer updates, shared-backbone interference, batch-normalization running statistics or label quality caused them. It does not justify changing loss weights or checkpoint selection automatically. Full-validation per-city/class decomposition and controlled frozen-BN/head-only experiments would distinguish those hypotheses. Artifacts retain paired target records, class/chip deltas and input/checkpoint hashes for follow-up.\n'
    destination.write_text(text,encoding='utf-8')
    print(json.dumps({'overall':overall,'cities':summary['cities']},indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--directory',type=Path,required=True)
    p.add_argument('--report',type=Path,required=True)
    a=p.parse_args();report(a.directory,a.report)
