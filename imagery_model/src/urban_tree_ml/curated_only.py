"""Curated full-chip ablation: fresh training, pinned baseline validation."""
import argparse
import hashlib
import json
import os
import tarfile
from pathlib import Path

import pandas as pd


def completed_chips(manifest, state):
    # A small completed crop must not promote the surrounding unreviewed full tile.
    return sorted({scene['validation_chip_id'] for scene in manifest['scenes']
                   if scene.get('validation_chip_id') and scene.get('splits') == ['train']
                   and (state['scene_reviews'].get(scene['scene_id'], {}).get('done')
                        or state['scene_reviews'].get(scene['scene_id'], {}).get('more_done'))})


def prepare(root, experiment, baseline):
    from urban_tree_ml.freeze_next_run import validate_frozen_city
    inputs = root / 'run-inputs' / experiment
    if (inputs / 'warm-start.json').exists():
        raise ValueError('Curated-only experiment must start from pretrained backbone, not a checkpoint')
    policy = {'baseline': baseline, 'mode': 'completed_full_training_chips_only', 'cities': {}}
    for city in ('ussfo', 'usbos'):
        state = validate_frozen_city(inputs / city)
        manifest = json.loads((inputs / city / 'manifest.json').read_text())
        ids = completed_chips(manifest, state)
        parent = root / 'runs' / baseline
        if not (parent / 'COMPLETE').exists():
            raise ValueError('Baseline incomplete')
        cfg = json.loads((parent / f'config-{city}.json').read_text())
        original = root / 'chips' / cfg['dataset']
        chips = pd.read_parquet(original / 'chips.parquet')
        train = chips[chips.split == 'train']
        selected = train[train.chip_id.isin(ids)]
        if selected.empty:
            raise ValueError(f'No completed training chips for {city}')
        # Pin only actual training IDs. Never infer reviewed status from default labels.
        policy['cities'][city] = {
            'chip_ids': sorted(selected.chip_id.tolist()), 'baseline_dataset': cfg['dataset'],
            'baseline_train_chips': len(train), 'selected_chips': len(selected),
            'baseline_selected_targets': int(selected.tree_count.sum()),
            'validation_sha256': {name: hashlib.sha256((original / name).read_bytes()).hexdigest()
                                  for name in ('chips.parquet', 'labels.parquet')},
        }
    with (inputs / 'curated-only.json').open('x') as stream:
        json.dump(policy, stream, indent=2)
    with tarfile.open(root / f'{experiment}-inputs.tar.gz', 'w:gz') as archive:
        archive.add(inputs, arcname=experiment)
    print(json.dumps(policy, indent=2))


def pin_validation(root, sources, policy):
    """Use the baseline's labels AND prebuilt validation targets/pixels for early stopping."""
    for city, destination in sources.items():
        spec = policy['cities'][city]
        original = root / 'chips' / spec['baseline_dataset']
        for name, digest in spec['validation_sha256'].items():
            if hashlib.sha256((original / name).read_bytes()).hexdigest() != digest:
                raise ValueError(f'Baseline validation hash mismatch: {city}/{name}')
            old = pd.read_parquet(original / name)
            new = pd.read_parquet(destination / name)
            validation = old[old.split == 'validation'].copy()
            if name == 'chips.parquet':
                validation['path'] = validation.path.map(lambda p: f'../{original.name}/{p}')
                if not all((destination / p).is_file() for p in validation.path):
                    raise ValueError('Missing frozen validation pixels')
            pd.concat([new[new.split != 'validation'], validation], ignore_index=True).to_parquet(
                destination / name, index=False)


def restrict_joint(root, experiment, policy):
    path = root / 'chips' / experiment / 'chips.parquet'
    frame = pd.read_parquet(path)
    allowed = {f'{city}:{chip}' for city, spec in policy['cities'].items() for chip in spec['chip_ids']}
    selected = frame[(frame.split != 'train') | frame.chip_id.isin(allowed)].copy()
    pd.testing.assert_frame_equal(frame[frame.split != 'train'], selected[selected.split != 'train'])
    audit = {}
    for city in policy['cities']:
        rows = selected[(selected.city == city) & (selected.split == 'train')]
        if rows.empty:
            raise ValueError(f'No training chips after materialization: {city}')
        audit[city] = {'chips': len(rows), 'targets': int(rows.tree_count.sum()),
                       'chip_ids': sorted(rows.source_chip_id.tolist())}
    selected.to_parquet(path, index=False)
    (root / 'runs' / experiment / 'curated-only-audit.json').write_text(json.dumps(
        {'policy': policy, 'actual_training': audit, 'test_evaluated': False}, indent=2))
    print('Curated-only actual training:', json.dumps({c: {k:v for k,v in a.items() if k != 'chip_ids'}
                                                    for c,a in audit.items()}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(os.environ['TREE_ML_DATA_ROOT']))
    parser.add_argument('--experiment', required=True)
    parser.add_argument('--baseline', required=True)
    args = parser.parse_args()
    prepare(args.root, args.experiment, args.baseline)
