"""Inference-only v9 comparison on the frozen 19-chip native-30 NYC validation set."""
import argparse
import hashlib
import json
from pathlib import Path
import time

import pandas as pd

from urban_tree_ml.config import ProjectConfig
from urban_tree_ml.evaluation import run_evaluation

BASE = 'nyc-naip-native30-v7-baseline'
PARENT = 'sf-boston-naip-swin-30cm-v9-fixed-pair-finetune'
RUN = 'nyc-naip-native30-v9-fixed-pair-zero-shot'
SHA = '392fbe1f422defe785dd80739bb74234a7a4e49b41c51e5640fbfc88b9b79539'


def digest(path):
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def main(root):
    import torch
    torch.set_num_threads(4)
    output = root / 'runs' / RUN
    if output.exists():
        raise ValueError('Refusing to overwrite an existing evaluation')
    checkpoint = root / 'runs' / PARENT / 'checkpoints/001.ckpt'
    assert digest(checkpoint) == SHA
    config = ProjectConfig.model_validate_json((root/'run-inputs'/BASE/'config.json').read_text())
    config.paths.root = root
    config.experiment = RUN
    config.training.batch_size = 1
    config.training.workers = 0
    chip_root = root/'chips'/config.dataset
    manifest = pd.read_parquet(chip_root/'chips.parquet')
    assert 'test' not in set(manifest.split)
    selected = manifest.loc[manifest.split == 'validation']
    assert len(selected) == selected.chip_id.nunique() == 19
    frozen = json.loads((root/'run-inputs/nyc-naip-swin-30cm-v8-adaptation/prepared-finetune.json').read_text())
    hashes = {x['path']: x['sha256'] for x in frozen['pixel_checksums']}
    receipt = {}
    for row in selected.itertuples():
        relative = row.path.replace('\\', '/')
        expected = hashes['chips/nyc-naip-swin-30cm-v8-adaptation-usnyc/' + relative]
        actual = digest(chip_root/relative)
        assert actual == expected
        receipt[relative] = actual
    plan = json.loads((root/'run-inputs'/BASE/'split-plan.json').read_text())
    assert not plan['test_evaluated'] and not plan['test_pixels_materialized']
    print('Verified checkpoint and all 19 frozen validation pixel hashes; CPU inference starting', flush=True)
    started = time.monotonic()
    result = run_evaluation(config, checkpoint, split='validation', cohort='validation-usnyc',
                            device_name='cpu', output_run=output)
    current = output/'evaluation/validation-usnyc'
    previous = root/'runs'/BASE/'evaluation/validation-usnyc'
    truth = pd.read_parquet(current/'ground-truth.parquet')
    old = pd.read_parquet(previous/'ground-truth.parquet')
    pd.testing.assert_frame_equal(truth, old, check_exact=True)
    assert len(truth) == 628
    assert json.loads((current/'taxonomy.json').read_text()) == json.loads((previous/'taxonomy.json').read_text())
    coverage = json.loads((current/'inference-coverage.json').read_text())
    assert coverage['complete'] and {x['chip_id'] for x in coverage['chips']} == set(selected.chip_id)
    predictions = pd.read_parquet(current/'predictions.parquet')
    counts = predictions.groupby('chip_id').size().to_dict()
    assert counts == {x['chip_id']: x['prediction_count'] for x in coverage['chips'] if x['prediction_count']}
    audit = dict(checkpoint_sha256=SHA, baseline=BASE, candidate=RUN, training_run=PARENT,
                 validation_pixel_sha256=receipt, chips=19, truth_rows=len(truth),
                 identical_frozen_truth=True, test_sealed=True, device='cpu',
                 seconds=round(time.monotonic()-started, 2),
                 predictions_sha256=digest(current/'predictions.parquet'), result=result)
    (output/'verification.json').write_text(json.dumps(audit, indent=2)+'\n', encoding='utf-8')
    (output/'COMPLETE').write_text('NYC validation-only inference and frozen-input audit completed.\n', encoding='utf-8')
    print(json.dumps({k: audit[k] for k in ['chips','truth_rows','seconds','checkpoint_sha256']}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    main(parser.parse_args().root.resolve())
