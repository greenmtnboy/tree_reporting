"""Common frozen-mask scoring of saved predictions; no inference or annotation writes."""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from urban_tree_ml.frozen_comparison import assert_compatible, match_frame, write_csv


def export_masks(root, baseline, candidate, output):
    output.mkdir(parents=True, exist_ok=False)
    receipt = {'baseline': baseline, 'candidate': candidate, 'cities': {}}
    for city in ('ussfo', 'usbos'):
        dirs = []
        for run in (baseline, candidate):
            cfg = json.loads((root/'runs'/run/f'config-{city}.json').read_text())
            directory = root/'chips'/cfg['dataset']
            frame = pd.read_parquet(directory/'chips.parquet')
            dirs.append((directory, frame[frame.split == 'validation'].set_index('chip_id')))
        (old_dir, old), (new_dir, new) = dirs
        if set(old.index) != set(new.index):
            raise ValueError('Validation cohorts differ')
        arrays, hashes = {}, {}
        for chip in sorted(old.index):
            original, current = old_dir/old.loc[chip, 'path'], new_dir/new.loc[chip, 'path']
            # Usually these resolve to the same pinned file; verify even if not.
            digest = hashlib.sha256(original.read_bytes()).hexdigest()
            if original.resolve() != current.resolve() and hashlib.sha256(current.read_bytes()).hexdigest() != digest:
                raise ValueError(f'Validation target pixels changed: {city}/{chip}')
            hashes[chip] = digest
            with np.load(original) as sample:
                for field in ('center', 'detection_mask'):
                    arrays[chip+'__'+field] = sample[field].squeeze()
        np.savez_compressed(output/f'{city}.npz', **arrays)
        receipt['cities'][city] = {'chip_npz_sha256': hashes,
            'export_sha256': hashlib.sha256((output/f'{city}.npz').read_bytes()).hexdigest()}
    (output/'provenance.json').write_text(json.dumps(receipt, indent=2))


def score_matched(frame, truth_count):
    tp = int((frame.truth_index >= 0).sum())
    unmatched = frame[frame.truth_index < 0]
    ignored = unmatched.detection_mask_value <= 0
    def metrics(fp):
        fn = truth_count-tp
        return {'tp': tp, 'fp': float(fp), 'fn': fn,
                'precision': tp/(tp+fp) if tp+fp else 0,
                'recall': tp/truth_count if truth_count else None,
                'f2': 5*tp/(5*tp+fp+4*fn) if 5*tp+fp+4*fn else 0}
    # Diagnostic only: target-side negative-loss weight, not actual dense focal loss.
    weight = unmatched.detection_mask_value * (1-unmatched.center_target)**4
    weight = weight.where(unmatched.center_target < 1, 0)
    return {'strict': metrics(len(unmatched)),
            'mask_aware': metrics(int((~ignored).sum())),
            'target_weighted_diagnostic': metrics(float(weight.sum())),
            'ignored_unmatched': int(ignored.sum()),
            'soft_center_unmatched': int(((~ignored) & (unmatched.center_target > 0)).sum())}


def build(root, baseline, candidate, masks, output):
    output.mkdir(parents=True, exist_ok=False)
    provenance = json.loads((masks/'provenance.json').read_text())
    if (provenance['baseline'], provenance['candidate']) != (baseline, candidate):
        raise ValueError('Mask source runs differ')
    results, rows = {}, []
    for city, cohort in [('ussfo','validation'), ('usbos','validation-usbos')]:
        if hashlib.sha256((masks/f'{city}.npz').read_bytes()).hexdigest() != provenance['cities'][city]['export_sha256']:
            raise ValueError('Mask archive hash mismatch')
        common = None
        metas, preds = [], []
        results[city] = {}
        for run in (baseline, candidate):
            directory = root/'runs'/run/'evaluation'/cohort
            meta = json.loads((directory/'evaluation-metadata.json').read_text())
            truth = pd.read_parquet(directory/'ground-truth.parquet').reset_index(drop=True)
            if common is None:
                common = truth
            else:
                pd.testing.assert_frame_equal(common, truth)
            pred = pd.read_parquet(directory/'predictions.parquet').reset_index(drop=True)
            metas.append(meta); preds.append(pred)
        assert_compatible(metas[0], metas[1], preds[0], preds[1])
        with np.load(masks/f'{city}.npz') as frozen:
            chip_ids = set(provenance['cities'][city]['chip_npz_sha256'])
            for run, meta, pred in zip((baseline,candidate), metas, preds):
                if not set(pred.chip_id) <= chip_ids or not set(common.chip_id) <= chip_ids:
                    raise ValueError('Predictions outside frozen cohort')
                # Re-sample COMMON target arrays, not historical per-run mask columns.
                pred = pred[pred.score >= .35].copy().reset_index(drop=True)
                for chip, group in pred.groupby('chip_id'):
                    x, y = group.output_x.to_numpy().astype(int), group.output_y.to_numpy().astype(int)
                    for field, column in [('center','center_target'),('detection_mask','detection_mask_value')]:
                        values = frozen[chip+'__'+field]
                        if (x<0).any() or (y<0).any() or (x>=values.shape[1]).any() or (y>=values.shape[0]).any():
                            raise ValueError('Prediction outside target grid')
                        pred.loc[group.index,column] = values[y,x]
                results[city][run] = {}
                for radius in (2.,4.):
                    scale = meta['config']['imagery']['resolution_m'] * meta['config']['targets']['output_stride']
                    matched = match_frame(pred, common, radius/scale)
                    result = score_matched(matched, len(common))
                    results[city][run][str(radius)] = result
                    write_csv(matched, output/f'{city}-{run}-{radius:g}m.csv')
                    for mode in ('strict','mask_aware','target_weighted_diagnostic'):
                        rows.append({'city':city,'run':run,'radius_m':radius,'mode':mode,
                                     **result[mode], 'ignored_unmatched':result['ignored_unmatched']})
    (output/'metrics.json').write_text(json.dumps(results, indent=2))
    (output/'mask-provenance.json').write_text(json.dumps(provenance, indent=2))
    write_csv(pd.DataFrame(rows), output/'scores.csv')
    lines = ['# Common-mask validation comparison', '',
             'Confidence 0.35. Identical v10 validation labels, pixels and masks for both models.',
             'Match known trees first; unmatched mask-zero predictions are ignored, NOT counted as correct.',
             'Recall is unchanged. Mask-aware precision is conditional on supervised regions, not discovery precision.',
             'Target-weighted diagnostic additionally uses mask*(1-center)^4 for unmatched negative weight; it is not standard F2 or the full dense training loss.', '',
             '| City | Run | Radius | Mode | Precision | Recall | F2 | Ignored unmatched |',
             '|---|---|---|---|---:|---:|---:|---:|']
    for row in rows:
        lines.append(f"| {row['city']} | {row['run']} | {row['radius_m']:g} m | {row['mode']} | {row['precision']:.1%} | {row['recall']:.1%} | {row['f2']:.1%} | {row['ignored_unmatched']} |")
    lines += ['', 'No test evaluation, retraining, or live annotation mutation. A manual discovery audit is still needed to establish whether ignored predictions identify real trees.']
    (output/'REPORT.md').write_text('\n'.join(lines), encoding='utf-8')
    (output/'COMPLETE').write_text('Common-mask comparison complete.\n')
    print('\n'.join(lines))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--baseline', required=True)
    parser.add_argument('--candidate', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--masks', type=Path)
    args = parser.parse_args()
    if args.masks:
        build(args.root, args.baseline, args.candidate, args.masks, args.output)
    else:
        export_masks(args.root, args.baseline, args.candidate, args.output)
