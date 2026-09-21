"""Stage random additional non-test review chips; apply with reviewer stopped.

Preparation never writes live state. Apply preserves current reviews byte-for-byte,
including annotations made while staging, and refuses changed scene manifests.
"""
import argparse
import hashlib
import json
import random
import shutil
from pathlib import Path

import pandas as pd

from urban_tree_ml.config import load_config
from urban_tree_ml.feedback import _write_json_atomic, snapshot_registration_annotations
from urban_tree_ml.quality import append_validation_chip_to_registration_review


def prepare(config, directory, dataset, stage, count=200, seed=20260911):
    stage.mkdir(parents=True, exist_ok=False)
    original = (directory / 'manifest.json').read_bytes()
    manifest = json.loads(original)
    if manifest['metadata'].get('curation_schema_version') != 2:
        raise ValueError('Requires per-tree curation storage')
    chips = pd.read_parquet(dataset / 'chips.parquet')
    labels = pd.read_parquet(dataset / 'labels.parquet', columns=['chip_id', 'split'])
    nonempty = set(labels.loc[labels['split'].isin(['train', 'validation']), 'chip_id'])
    existing = {scene.get('validation_chip_id') for scene in manifest['scenes']}
    eligible = chips[chips['split'].isin(['train', 'validation']) & chips.chip_id.isin(nonempty)
                     & ~chips.chip_id.isin(existing)].drop_duplicates('chip_id')
    selected = random.Random(seed).sample(sorted(eligible.chip_id.tolist()), count)
    inventory = pd.read_parquet(config.paths.root / 'inventory' / config.inventory.city.lower() / 'inventory.parquet')
    before_scenes = len(manifest['scenes'])
    for index, chip in enumerate(selected):
        result = append_validation_chip_to_registration_review(
            config, config.imagery.local_raster, stage, chip, pd.DataFrame(),
            _manifest=manifest, _persist=False, _inventory=inventory)
        assert result['added']
        if (index + 1) % 25 == 0:
            print(config.inventory.city, index + 1, 'staged', flush=True)
    report = dict(city=config.inventory.city, seed=seed, dataset=str(dataset),
                  before_sha256=hashlib.sha256(original).hexdigest(),
                  before_scenes=before_scenes, after_scenes=len(manifest['scenes']),
                  selected=selected,
                  splits=eligible[eligible.chip_id.isin(selected)]['split'].value_counts().to_dict())
    _write_json_atomic(stage / 'manifest.json', manifest)
    _write_json_atomic(stage / 'report.json', report)
    return report


def apply(config, directory, stage):
    report = json.loads((stage / 'report.json').read_text())
    original = (directory / 'manifest.json').read_bytes()
    if hashlib.sha256(original).hexdigest() != report['before_sha256']:
        raise ValueError('Review collection changed during preparation; rebuild the batch')
    backup = stage / 'before-apply'
    backup.mkdir(exist_ok=False)
    for name in ['manifest.json', 'reviews.json', 'training-feedback.json']:
        if (directory / name).exists():
            shutil.copy2(directory / name, backup / name)
    for image in (stage / 'images').glob('*.png'):
        target = directory / 'images' / image.name
        if target.exists():
            raise ValueError(f'Refusing to overwrite existing image: {target}')
        shutil.copy2(image, target)
    _write_json_atomic(directory / 'manifest.json', json.loads((stage / 'manifest.json').read_text()))
    assert (directory / 'reviews.json').read_bytes() == (backup / 'reviews.json').read_bytes()
    snapshot_registration_annotations(config, config.imagery.local_raster, review_dir=directory)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    for city, filename, review in [('ussfo', 'sf_naip_citywide_curated.yaml', 'ussfo-2022-mosaic'),
                                    ('usbos', 'boston_naip_external.yaml', 'usbos-2023-external')]:
        cfg = load_config(Path('configs') / filename)
        directory = cfg.paths.root / 'qa/registration' / review
        stage = args.stage / city
        if args.apply:
            report = apply(cfg, directory, stage)
        else:
            dataset = cfg.paths.root / 'chips' / f'sf-boston-naip-curation-v8-date-finetune-{city}'
            report = prepare(cfg, directory, dataset, stage)
        print({k: v for k, v in report.items() if k != 'selected'}, flush=True)
