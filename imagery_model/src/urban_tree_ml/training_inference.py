"""Full training-cohort inference using an immutable completed checkpoint; never fit."""
import argparse
import hashlib
import json
import os
import tarfile
from pathlib import Path

import pandas as pd


def prepare(root, experiment, source):
    parent = root / 'runs' / source
    assert (parent / 'COMPLETE').is_file(), 'Source must be complete'
    checkpoint = parent / 'checkpoints' / Path(json.loads(
        (parent / 'training-result.json').read_text())['best_checkpoint']).name
    manifest = {'source_run': source, 'checkpoint': checkpoint.relative_to(root).as_posix(),
                'checkpoint_sha256': hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
                'inference_only': True, 'split': 'train', 'cities': {}}
    for city in ['ussfo', 'usbos']:
        assert not (parent / 'evaluation' / f'train-{city}').exists(), 'Do not overwrite inference'
        config_path = parent / f'config-{city}.json'
        config = json.loads(config_path.read_text())
        chips_path = root / 'chips' / config['dataset'] / 'chips.parquet'
        chips = pd.read_parquet(chips_path)
        ids = sorted(chips.loc[chips.split == 'train', 'chip_id'].tolist())
        assert ids and len(ids) == len(set(ids))
        manifest['cities'][city] = {'chip_ids': ids, 'config_sha256': hashlib.sha256(
            config_path.read_bytes()).hexdigest(), 'chips_sha256': hashlib.sha256(
            chips_path.read_bytes()).hexdigest()}
    output = root / 'run-inputs' / experiment
    output.mkdir(parents=True, exist_ok=False)
    (output / 'inference.json').write_text(json.dumps(manifest, indent=2))
    (output / 'FREEZE_COMPLETE').write_text('Saved training cohort only; no live annotation reads.\n')
    with tarfile.open(root / f'{experiment}-inputs.tar.gz', 'w:gz') as archive:
        archive.add(output, arcname=experiment)
    print({city: len(data['chip_ids']) for city, data in manifest['cities'].items()})


def infer(root, experiment):
    from urban_tree_ml.config import ProjectConfig
    from urban_tree_ml.evaluation import run_evaluation
    import torch

    assert torch.cuda.is_available()
    assert torch.ones(32, device='cuda').sum().item() == 32
    manifest = json.loads((root / 'run-inputs' / experiment / 'inference.json').read_text())
    assert manifest['split'] == 'train' and manifest['inference_only']
    checkpoint = root / manifest['checkpoint']
    assert hashlib.sha256(checkpoint.read_bytes()).hexdigest() == manifest['checkpoint_sha256']
    parent = root / 'runs' / manifest['source_run']
    for city, data in manifest['cities'].items():
        path = parent / f'config-{city}.json'
        assert hashlib.sha256(path.read_bytes()).hexdigest() == data['config_sha256']
        config = ProjectConfig.model_validate_json(path.read_text())
        chips_path = root / 'chips' / config.dataset / 'chips.parquet'
        assert hashlib.sha256(chips_path.read_bytes()).hexdigest() == data['chips_sha256']
        assert not (parent / 'evaluation' / f'train-{city}').exists()
    run = root / 'runs' / experiment
    run.mkdir(parents=True, exist_ok=False)
    (run / 'inference-provenance.json').write_text(json.dumps(manifest, indent=2))
    for city, data in manifest['cities'].items():
        config = ProjectConfig.model_validate_json((parent / f'config-{city}.json').read_text())
        print(f'Full training inference: {city}, {len(data["chip_ids"])} chips', flush=True)
        run_evaluation(config, checkpoint, split='train', device_name='cuda', cohort=f'train-{city}')
    (run / 'COMPLETE').write_text('Full training inference complete; no training or test evaluation.\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--experiment', required=True)
    parser.add_argument('--root', type=Path, default=Path(os.environ.get('TREE_ML_DATA_ROOT', '.')))
    parser.add_argument('--source')
    parser.add_argument('--prepare', action='store_true')
    parser.add_argument('configs', nargs='*')
    args = parser.parse_args()
    for name in [args.experiment, args.source or '']:
        if any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-' for c in name):
            parser.error('Invalid run name')
    if args.prepare:
        if not args.source:
            parser.error('--source is required for preparation')
        prepare(args.root, args.experiment, args.source)
    else:
        infer(args.root, args.experiment)
