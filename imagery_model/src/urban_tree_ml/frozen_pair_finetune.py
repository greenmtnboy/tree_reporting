"""Two-view continuation on an immutable parent's SF/Boston development chips."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import pandas as pd

from urban_tree_ml.prepared_finetune import config_from_recipe, resolve, sha


def validate(root: Path, experiment: str, *, pixels: bool = False):
    inputs = root / 'run-inputs' / experiment
    recipe = json.loads((inputs / 'frozen-parent.json').read_text())
    if recipe['experiment'] != experiment or recipe['test_evaluated'] is not False:
        raise ValueError('Frozen recipe identity or sealed test policy mismatch')
    if set(recipe['configs']) != {'ussfo', 'usbos'}:
        raise ValueError('This comparison must contain SF and Boston only')
    for relative, expected in recipe['checksums'].items():
        if sha(resolve(root, relative)) != expected:
            raise ValueError('Frozen parent checksum mismatch: ' + relative)
    checkpoint = resolve(root, recipe['checkpoint'])
    if sha(checkpoint) != recipe['checkpoint_sha256']:
        raise ValueError('Warm-start checkpoint hash mismatch')
    configs = []
    pixel_receipts = []
    for city in ('ussfo', 'usbos'):
        config = config_from_recipe(root, recipe['configs'][city], experiment)
        if (config.inventory.city.lower() != city or config.training.augmentation != 'fixed_pair_v1'
                or config.training.random_dihedral):
            raise ValueError('Wrong city or augmentation policy')
        directory = root / 'chips' / config.dataset
        frame = pd.read_parquet(directory / 'chips.parquet')
        if frame.chip_id.duplicated().any() or not {'train', 'validation'} <= set(frame.split):
            raise ValueError('Invalid frozen chip identities/splits')
        actual = {k: int(v) for k, v in frame.groupby('split').size().items()}
        if actual != recipe['counts'][city]:
            raise ValueError('Frozen split counts changed')
        # Only development pixels are opened. Test IDs remain metadata only.
        if pixels:
            for row in frame[frame.split.isin(['train', 'validation'])].itertuples():
                path = directory / row.path
                relative = path.relative_to(root).as_posix()
                resolve(root, relative)  # Reject traversal before opening pixels.
                pixel_receipts.append(dict(path=relative, sha256=sha(path)))
        configs.append(config)
    return recipe, configs, pixel_receipts


def run(root: Path, experiment: str):
    from urban_tree_ml.joint import combine_manifests, train_and_evaluate
    directory = root / 'runs' / experiment
    destination = root / 'chips' / experiment
    if directory.exists() or destination.exists():
        raise ValueError('Run/dataset exists; refusing duplicate training')
    recipe, configs, receipts = validate(root, experiment, pixels=True)
    directory.mkdir(parents=True)
    (directory / 'frozen-pixel-receipt.json').write_text(json.dumps(receipts, indent=2))
    combine_manifests({c.inventory.city.lower(): root / 'chips' / c.dataset for c in configs}, destination)
    # Preserve source test assignments, but exclude them from the training manifest.
    manifest = pd.read_parquet(destination / 'chips.parquet')
    manifest[manifest.split.isin(['train', 'validation'])].to_parquet(destination / 'chips.parquet', index=False)
    for config in configs:
        (directory / f'config-{config.inventory.city.lower()}.json').write_text(config.model_dump_json(indent=2))
    parent_audit = root / 'runs' / recipe['parent'] / 'curation-audit.json'
    if parent_audit.exists():
        (directory / 'curation-audit.json').write_bytes(parent_audit.read_bytes())
    (directory / 'comparison-design.json').write_text(json.dumps(dict(
        parent=recipe['parent'], frozen_labels=True, unchanged_test=True,
        source_train_windows=sum(recipe['counts'][c]['train'] for c in recipe['configs']),
        caveat='Versus parent checkpoint: additional fine-tuning plus augmentation, not an augmentation-only causal ablation.',
    ), indent=2))
    print(f'Frozen parent verified: {len(receipts)} development payload hashes; SF/Boston only; test sealed', flush=True)
    train_and_evaluate(configs, experiment, directory)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--experiment', required=True)
    parser.add_argument('configs', nargs='*')
    args = parser.parse_args()
    run(Path(os.environ['TREE_ML_DATA_ROOT']), args.experiment)
