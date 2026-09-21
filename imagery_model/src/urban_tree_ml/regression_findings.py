"""Summarize completed regression diagnostics, including the dense Boston counterpoint."""
import argparse
from pathlib import Path
import json

import pandas as pd

from urban_tree_ml.frozen_comparison import write_csv


def summarize(output):
    provenance = json.loads((output / 'provenance.json').read_text())
    paired = Path(provenance['paired_source'])
    trees = pd.read_parquet(output / 'tree-diagnostics.parquet')
    chips = pd.read_csv(paired / 'chip-scores.csv')
    chips = chips[chips.radius_m == 4]
    bos = trees[trees.city == 'usbos']
    dense = set(bos[bos.chip_tree_count > 120].chip_id)
    cohort_rows = []
    for model in ['old', 'new']:
        for cohort, selected in [('dense_121plus', True), ('at_most_120', False)]:
            c = chips[(chips.city == 'usbos') & (chips.model == model) & (chips.chip_id.isin(dense) == selected)]
            tp, fp, fn = int(c.tp.sum()), int(c.unmatched_predictions.sum()), int(c.missed.sum())
            cohort_rows.append({'model': model, 'cohort': cohort, 'chips': len(c), 'tp': tp,
                'fp': fp, 'fn': fn, 'precision': tp / (tp+fp), 'recall': tp/(tp+fn), 'f2': 5*tp/(5*tp+fp+4*fn)})
    write_csv(pd.DataFrame(cohort_rows), output / 'boston-density-scores.csv')
    grouped = bos.groupby('chip_id').agg(trees=('tree_id','size'), old=('old_match','sum'), new=('new_match','sum'))
    grouped['net_change'] = grouped.new - grouped.old
    lines = ['# Findings: why did v9 score worse?', '',
        'Detection only, identical frozen v9 labels and paired validation chips. Default comparison: 4 m / confidence 0.35. These are diagnostic associations, not causal attribution.', '',
        '## 1. Most lost matches are confidence-sensitive', '',
        '| City | v5 hit / v9 miss | Recovered by v9 at 0.20 | v5 miss / v9 hit |',
        '|---|---:|---:|---:|']
    for city, g in trees.groupby('city'):
        lost = g[g.outcome == 'regression']
        recovered = int((lost.diagnosis == 'recovered_at_020').sum())
        lines.append(f'| {city} | {len(lost)} | {recovered} ({recovered/len(lost):.1%}) | {int((g.outcome == "improvement").sum())} |')
    lines += ['', 'Lower thresholds bring more false positives too. On the existing exploratory threshold grid, SF peaks at 0.30 (v5 F2 44.5%, v9 43.5%) and Boston at 0.25 (v5 37.6%, v9 36.3%). Thus threshold selection narrows—but does not erase—the model gap. These tuned values are not test results.', '',
        '## 2. Boston improves outside the densest chips', '',
        f'The {len(dense)} chips with more than 120 retained labels contain {int(bos.chip_id.isin(dense).sum())} eligible trees; {int(bos[bos.chip_id.isin(dense)].tree_id.str.startswith("arb-").sum())} have arboretum-prefixed IDs.', '',
        '| Cohort | Model | Chips | Matched trees | Precision | Recall | F2 |', '|---|---|---:|---:|---:|---:|---:|']
    for r in cohort_rows:
        lines.append(f'| {r["cohort"]} | {r["model"]} | {r["chips"]} | {r["tp"]} | {r["precision"]:.1%} | {r["recall"]:.1%} | {r["f2"]:.1%} |')
    lines += ['', 'This is a post-hoc diagnostic split, not a replacement benchmark. Dense inventory points need not correspond to separately visible crowns.', '',
        '### Biggest Boston regressions (including dense chips)', '']
    for chip, r in grouped.sort_values('net_change').head(10).iterrows():
        url = f'http://127.0.0.1:8765/compare?run={provenance["candidate"]}%3A%3Avalidation-usbos&chip={chip}'
        lines.append(f'- [{chip}]({url}): {r.trees} labels, {r.old} → {r.new} matches.')
    lines += ['', '## 3. Large-tree losses overlap with the density issue', '',
        '| City | DBH bin | Labels | v5 recall | v9 recall |', '|---|---|---:|---:|---:|']
    for (city, size), g in trees.groupby(['city', 'dbh_bin']):
        lines.append(f'| {city} | {size} | {len(g)} | {g.old_match.mean():.1%} | {g.new_match.mean():.1%} |')
    lines += ['', 'Boston 32+ inch trees by density:', '']
    for density, g in bos[bos.dbh_bin == '32plus'].groupby('density_bin'):
        lines.append(f'- {density}: {len(g)} labels, {int(g.old_match.sum())} → {int(g.new_match.sum())} hits.')
    lines += ['', '## 4. Positive training supervision changed, but not catastrophically', '',
        '| City | Model | Retained training labels | Ignored feedback points | Post-imagery plantings |', '|---|---|---:|---:|---:|']
    for r in json.loads((output / 'supervision.json').read_text()):
        lines.append(f'| {r["city"]} | {r["model"]} | {r["train_labels"]} | {r["feedback_ignored_points"]} | {r["late_plantings"]} |')
    lines += ['', 'Retained training labels fell about 2.0% in SF and 4.0% in Boston. This includes intentional planting-date exclusions, curation, and changes in collision eligibility—not only manual tagging.',
        'Code audit: feedback-excluded trees (not-tree/uncertain/duplicate/occluded) become ignored locations, masking nearby center loss. They do not automatically become negatives. Manual confirmed-background masks can override; retained positive centers always win. Exact per-training-pixel effects were not recomputed here.', '',
        '## Suggested next action', '',
        '1. Review the dense Boston regressions above separately from the sparse shortlist. Determine whether v5 was rewarded for multiple inventory points beneath one canopy while v9 predicts fewer visible crowns.',
        '2. Use the 20 sparse examples per city (10 net regressions + 10 improvements) in REPORT.md to compare both models without cherry-picking only failures.',
        '3. Inspect lower-confidence overlays before annotating a missed tree as a true localization failure. Keep the default benchmark fixed while exploring thresholds.',
        '4. Do not conclude that curation is harmful. A controlled retrain/ablation would be needed to separate annotation changes from planting-date filtering and training variability.', '',
        'The comparison UI may use live labels. The saved tree-diagnostics.parquet is the authoritative frozen per-tree analysis. No live review tags, annotations, or server state were changed.']
    (output / 'FINDINGS.md').write_text('\n'.join(lines), encoding='utf-8')
    print(output / 'FINDINGS.md')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('output', type=Path)
    summarize(parser.parse_args().output)
