"""Read-only diagnostics on an immutable paired uplift export; no GPU or live reviews."""
import argparse
import hashlib
import json
from pathlib import Path
from urllib.parse import urlencode

import numpy as np
import pandas as pd

from urban_tree_ml.frozen_comparison import match_frame, write_csv


def detected(pred, truth, radius, threshold, metres):
    matched = match_frame(pred.reset_index(drop=True), truth, radius / metres)
    return set(matched.loc[(matched.score >= threshold) & (matched.truth_index >= 0), 'truth_index'])


def tree_diagnostics(old, new, truth, metres=1.2):
    truth = truth.reset_index(drop=True)
    old4 = detected(old, truth, 4, .35, metres)
    new4 = detected(new, truth, 4, .35, metres)
    low = detected(new, truth, 4, .2, metres)
    lowest = detected(new, truth, 4, .1, metres)
    wide = detected(new, truth, 8, .35, metres)
    old2 = detected(old, truth, 2, .35, metres)
    new2 = detected(new, truth, 2, .35, metres)
    result = truth.copy()
    result['old_match'] = result.index.isin(old4)
    result['new_match'] = result.index.isin(new4)
    result['old_match_2m'] = result.index.isin(old2)
    result['new_match_2m'] = result.index.isin(new2)
    result['outcome'] = np.select([
        result.old_match & ~result.new_match, ~result.old_match & result.new_match,
        result.old_match & result.new_match], ['regression', 'improvement', 'both_hit'], 'both_miss')
    result['diagnosis'] = ''
    for i in old4 - new4:
        result.at[i, 'diagnosis'] = ('recovered_at_020' if i in low else
            'recovered_at_010' if i in lowest else 'recovered_at_8m' if i in wide else
            'not_recovered')
    groups = {k: g for k, g in new.groupby('chip_id')}
    for chip, targets in result.groupby('chip_id'):
        p = groups.get(chip, new.iloc[:0])
        for i, t in targets.iterrows():
            distance = np.hypot(p.output_x - t.output_x, p.output_y - t.output_y) * metres
            near = p.loc[distance <= 4]
            confident = distance[p.score >= .35]
            result.at[i, 'new_max_score_within_4m'] = near.score.max() if len(near) else np.nan
            result.at[i, 'new_nearest_confident_m'] = confident.min() if len(confident) else np.nan
    return result


def review_maps(directory):
    manifest = json.loads((directory / 'manifest.json').read_text())
    raw = json.loads((directory / 'reviews.json').read_text())
    records = raw.get('tree_reviews')
    if records is None:
        samples = {s['sample_id']: s for s in manifest['samples']}
        records = {str(samples[sid]['tree_id']): value for sid, value in raw.get('reviews', {}).items() if sid in samples}
    done = {k for k, v in raw.get('scene_reviews', {}).items() if v.get('done')}
    done_trees = {str(s['tree_id']) for s in manifest['samples'] if s.get('scene_id') in done}
    return records, done_trees


def build(root, paired, output):
    output.mkdir(parents=True, exist_ok=False)
    provenance = json.loads((paired / 'manifest.json').read_text())
    old_run, new_run = provenance['baseline'], provenance['candidate']
    all_trees, slices, shortlist, supervision, summaries = [], [], [], [], []
    hashes = {}
    for city in ['ussfo', 'usbos']:
        print('Diagnosing', city, flush=True)
        data = {}
        for model in ['old', 'new']:
            directory = paired / city / model
            for filename in ['ground-truth.parquet', 'predictions.parquet', 'evaluation-metadata.json']:
                path = directory / filename
                hashes[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
            data[model] = pd.read_parquet(directory / 'predictions.parquet')
        truth = pd.read_parquet(paired / city / 'new/ground-truth.parquet')
        ids = provenance['cities'][city]['common_chips']
        truth = truth[truth.chip_id.isin(ids)].reset_index(drop=True)
        meta = json.loads((paired / city / 'new/evaluation-metadata.json').read_text())
        metres = meta['config']['imagery']['resolution_m'] * meta['config']['targets']['output_stride']
        rows = tree_diagnostics(*(data[m][data[m].chip_id.isin(ids)] for m in ['old', 'new']), truth, metres)
        rows['tree_id'] = rows.tree_id.astype(str)
        rows['city'] = city
        inv = pd.read_parquet(root / 'run-inputs' / new_run / city / 'inventory.parquet')
        inv['tree_id'] = inv.tree_id.astype(str)
        names = inv.drop_duplicates('tree_id').set_index('tree_id')
        rows['source_species'] = rows.tree_id.map(names.source_species)
        rows['genus'] = rows.tree_id.map(names.genus)
        rows['dbh_in'] = np.expm1(rows.dbh_log1p)
        rows['dbh_bin'] = pd.cut(rows.dbh_in, [-np.inf, 8, 16, 32, np.inf],
            labels=['under8', '8to16', '16to32', '32plus'], right=False).astype('object').fillna('unknown')
        density = rows.groupby('chip_id').size()
        rows['chip_tree_count'] = rows.chip_id.map(density)
        rows['density_bin'] = pd.cut(rows.chip_tree_count, [0, 20, 60, 120, np.inf],
            labels=['1to20', '21to60', '61to120', '121plus']).astype(str)
        old_records, old_done = review_maps(root / 'run-inputs' / old_run / city)
        new_records, new_done = review_maps(root / 'run-inputs' / new_run / city)
        rows['done_coverage'] = np.where(rows.tree_id.isin(new_done), 'in_done_scene', 'outside_done_scenes')
        rows['curation_status'] = rows.tree_id.map(lambda tid: new_records.get(tid, {}).get('status', 'unreviewed'))
        rows['status_changed'] = rows.tree_id.map(lambda tid: old_records.get(tid, {}).get('status') != new_records.get(tid, {}).get('status'))
        old_truth = pd.read_parquet(paired / city / 'old/ground-truth.parquet')
        old_truth['tree_id'] = old_truth.tree_id.astype(str)
        joined = rows.merge(old_truth[['tree_id', 'chip_id', 'output_x', 'output_y']],
            on=['tree_id', 'chip_id'], how='left', suffixes=('', '_old'), validate='one_to_one')
        joined['label_move_m'] = np.hypot(joined.output_x - joined.output_x_old, joined.output_y - joined.output_y_old) * metres
        joined['label_change'] = np.select([joined.output_x_old.isna(), joined.label_move_m > .3],
            ['newly_eligible', 'moved_over_030m'], 'stable_within_030m')
        rows = joined
        all_trees.append(rows)
        for dimension in ['dbh_bin', 'density_bin', 'genus', 'done_coverage', 'label_change', 'curation_status']:
            for key, group in rows.groupby(dimension, dropna=False):
                slices.append({'city': city, 'dimension': dimension, 'slice': str(key), 'trees': len(group),
                    'old_hits': int(group.old_match.sum()), 'new_hits': int(group.new_match.sum()),
                    'old_recall': group.old_match.mean(), 'new_recall': group.new_match.mean(),
                    'regressions': int((group.outcome == 'regression').sum()),
                    'improvements': int((group.outcome == 'improvement').sum())})
        for model, run in [('old', old_run), ('new', new_run)]:
            directory = root / 'chips' / f'{run}-{city}'
            labels = pd.read_parquet(directory / 'labels.parquet')
            chips = pd.read_parquet(directory / 'chips.parquet')
            train = chips[chips.split == 'train']
            feedback = json.loads((root / 'run-inputs' / run / city / 'training-feedback.json').read_text())
            exclusions = pd.DataFrame(feedback['exclusions'])
            supervision.append({'city': city, 'model': model, 'train_chips': len(train),
                'train_labels': int((labels.split == 'train').sum()),
                'collision_excluded_points': int(train.collision_excluded_count.sum()),
                'feedback_ignored_points': int(train.feedback_ignored_count.sum()),
                'late_plantings': int(train.get('post_imagery_planting_count', pd.Series(dtype=int)).sum()),
                'train_exclusion_reasons': exclusions[exclusions.split == 'train'].reason.value_counts().to_dict() if len(exclusions) else {},
                'regions': len(feedback.get('region_overrides', []))})
        chips = rows.groupby('chip_id').agg(trees=('tree_id', 'size'),
            regressions=('outcome', lambda x: int((x == 'regression').sum())),
            improvements=('outcome', lambda x: int((x == 'improvement').sum())),
            label_moves=('label_change', lambda x: int((x == 'moved_over_030m').sum())))
        chips['net_lost'] = chips.regressions - chips.improvements
        # Bounded sparse examples, ordered by total losses/gains, not percentages.
        candidates = chips[(chips.trees >= 5) & (chips.trees <= 60)]
        selected = []
        for kind, ordered in [('regression', candidates[candidates.net_lost > 0].sort_values(['net_lost', 'regressions'], ascending=False)),
                              ('improvement', candidates[candidates.net_lost < 0].sort_values(['net_lost', 'improvements'], ascending=[True, False]))]:
            for chip, item in ordered.head(10).iterrows():
                run_id = new_run if city == 'ussfo' else new_run + '::validation-usbos'
                selected.append({'city': city, 'kind': kind, 'chip_id': chip, **item.to_dict(),
                    'url': 'http://127.0.0.1:8765/compare?' + urlencode({'run': run_id, 'chip': chip})})
        shortlist.extend(selected)
        reg = rows[rows.outcome == 'regression']
        summaries.append({'city': city, 'trees': len(rows), 'outcomes': rows.outcome.value_counts().to_dict(),
            'regression_diagnoses': reg.diagnosis.value_counts().to_dict(),
            'regressions_with_confident_neighbor_4m': int((reg.new_max_score_within_4m >= .35).sum())})
    trees = pd.concat(all_trees, ignore_index=True)
    trees.to_parquet(output / 'tree-diagnostics.parquet', index=False)
    write_csv(pd.DataFrame(slices), output / 'recall-slices.csv')
    write_csv(pd.DataFrame(shortlist), output / 'review-shortlist.csv')
    (output / 'supervision.json').write_text(json.dumps(supervision, indent=2))
    (output / 'summary.json').write_text(json.dumps(summaries, indent=2))
    (output / 'provenance.json').write_text(json.dumps({'paired_source': str(paired), 'input_hashes': hashes,
        'baseline': old_run, 'candidate': new_run, 'threshold': .35, 'radius_m': 4,
        'test_evaluated': False, 'live_curation_modified': False}, indent=2))
    lines = ['# v5 → v9 detection regression analysis', '',
        'Same paired validation chips and frozen v9 labels; confidence 0.35, one-to-one matches within 4 m.',
        'No training, new inference, test access, or annotation writes. Density means retained labeled trees per chip, not measured canopy cover.', '']
    for summary in summaries:
        lines += [f"## {summary['city']}", '', f"Outcomes: {summary['outcomes']}",
            f"Lost-match diagnostics: {summary['regression_diagnoses']}",
            f"Lost matches with a >=0.35 candidate inside 4 m: {summary['regressions_with_confident_neighbor_4m']} (one-to-one competition).", '']
    curves = pd.read_csv(paired / 'threshold-curves.csv')
    lines += ['## Threshold sensitivity', '', '| City | Model | Threshold | Precision | Recall | F2 |', '|---|---|---:|---:|---:|---:|']
    for city in ['ussfo', 'usbos']:
        for model in ['old', 'new']:
            curve = curves[(curves.city == city) & (curves.model == model) & (curves.radius_m == 4)]
            for _, r in curve[curve.threshold.isin([.2, .35]) | (curve.index == curve.f2.idxmax())].iterrows():
                lines.append(f'| {city} | {model} | {r.threshold:.2f} | {r.precision:.1%} | {r.recall:.1%} | {r.f2:.1%} |')
    lines += ['', 'Threshold maxima are exploratory validation tuning, not held-out improvements.', '', '## Sparse review examples', '',
        'Ten net-regression and ten net-improvement chips per city where available; 5–60 eligible labels, ranked by total count. Links open the existing compare view; that view may show live labels rather than this frozen analysis.', '']
    for r in shortlist:
        lines.append(f"- {r['city']} {r['kind']}: [{r['chip_id']}]({r['url']}) — {r['trees']} labels, {r['regressions']} lost / {r['improvements']} gained; {r['label_moves']} moved labels.")
    lines += ['', '## Interpretation limits', '',
        'Recovery at a lower threshold is a matching outcome, not proof the same physical prediction survived. The 8 m bucket is evaluated only after 0.20/0.10 recovery and suggests proximity sensitivity, not a proven displacement.',
        'A confident candidate near a missed tree can be assigned to a neighbor; raw proximity must not be counted as a recovered one-to-one match.',
        'Curation-status and label-motion associations are descriptive. Training curation, planting-date exclusions, collision changes, and training stochasticity are confounded; this does not establish causation.',
        'Training chip summaries quantify retained supervision, not exact negative-loss pixels. Those require the frozen chip arrays; evaluation masks are not training-step penalties.',
        'See tree-diagnostics.parquet, recall-slices.csv, supervision.json, and the paired threshold-curves.csv for the full breakdown.']
    (output / 'REPORT.md').write_text('\n'.join(lines), encoding='utf-8')
    (output / 'COMPLETE').write_text('Read-only regression diagnostics complete.\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--paired', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    build(args.root, args.paired, args.output)
