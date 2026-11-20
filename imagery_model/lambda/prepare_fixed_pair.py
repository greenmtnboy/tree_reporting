"""Freeze a two-view experiment from v7; never reads live curation or allocates compute."""
import argparse
import json
from pathlib import Path
import shutil
import tarfile

import pandas as pd

from urban_tree_ml.prepared_finetune import sha
from urban_tree_ml.frozen_pair_finetune import validate
from prepare_nyc_adaptation import portable

PARENT = 'sf-boston-naip-swin-30cm-v7-finetune'
PARENT_SHA = 'b04d57130251dcbfffab4857e6db6aa98c16661785d145be92d2490f52218ee6'


def prepare(root, experiment):
    inputs = root / 'run-inputs' / experiment
    if inputs.exists():
        raise ValueError('Freeze already exists; inspect rather than overwrite')
    previous = root / 'run-inputs' / PARENT
    checkpoint = f'runs/{PARENT}/checkpoints/007.ckpt'
    if sha(root / checkpoint) != PARENT_SHA:
        raise ValueError('Wrong v7 checkpoint')
    configs, checksums, counts = {}, {}, {}
    for city in ('ussfo', 'usbos'):
        raw = json.loads((root / 'runs' / PARENT / f'config-{city}.json').read_text())
        cfg = portable(raw, root)
        # Use the normalization actually embedded with the parent's chips,
        # rather than its historical external-reference path (not collected locally).
        cfg['reference']['normalization_path'] = f'chips/{PARENT}-ussfo/normalization.json'
        cfg['training'].update(augmentation='fixed_pair_v1', random_dihedral=False,
                               learning_rate=3e-5, epochs=80, batch_size=2, accumulate_grad_batches=16)
        configs[city] = cfg
        data = root / 'chips' / cfg['dataset']
        norm = json.loads((data / 'normalization.json').read_text())
        reference = json.loads((root / cfg['reference']['normalization_path']).read_text())
        if any(norm[k] != reference[k] for k in ('mean', 'std')):
            raise ValueError('Parent city normalizations differ')
        counts[city] = {k: int(v) for k, v in pd.read_parquet(data / 'chips.parquet').groupby('split').size().items()}
        pinned = [data / name for name in ('chips.parquet', 'labels.parquet', 'normalization.json',
                                          'summary.json', 'boundary-shift-build.json')]
        pinned += [root / cfg['reference'][key] for key in ('taxonomy_path', 'normalization_path')]
        pinned += [root / 'runs' / PARENT / f'config-{city}.json']
        for path in pinned:
            checksums[path.relative_to(root).as_posix()] = sha(path)
    shutil.copytree(previous, inputs)
    # Frozen city bundles remain byte-identical. Only the warm-start/augmentation recipe changes.
    warm = json.loads((inputs / 'warm-start.json').read_text())
    warm.update(checkpoint=checkpoint, checkpoint_sha256=PARENT_SHA, learning_rate=3e-5, epochs=80)
    (inputs / 'warm-start.json').write_text(json.dumps(warm, indent=2))
    recipe = dict(experiment=experiment, parent=PARENT, checkpoint=checkpoint,
                  checkpoint_sha256=PARENT_SHA, configs=configs, checksums=checksums,
                  counts=counts, test_evaluated=False)
    (inputs / 'frozen-parent.json').write_text(json.dumps(recipe, indent=2))
    validate(root, experiment)
    with tarfile.open(root / f'{experiment}-inputs.tar.gz', 'w:gz') as archive:
        archive.add(inputs, arcname=experiment)
    print(json.dumps(dict(experiment=experiment, counts=counts,
                         presentations_per_epoch=2*sum(c['train'] for c in counts.values()))), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--experiment', required=True)
    args = parser.parse_args()
    if not args.experiment or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-' for c in args.experiment):
        parser.error('Invalid experiment name')
    prepare(args.root, args.experiment)
