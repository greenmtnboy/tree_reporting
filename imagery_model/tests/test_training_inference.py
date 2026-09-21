import json

import pandas as pd
import pytest

from urban_tree_ml.training_inference import prepare


def test_prepare_only_training_and_no_overwrite(tmp_path):
    parent = tmp_path / 'runs' / 'source'
    (parent / 'checkpoints').mkdir(parents=True)
    (parent / 'COMPLETE').touch()
    (parent / 'checkpoints/035.ckpt').write_bytes(b'fixture checkpoint')
    (parent / 'training-result.json').write_text(json.dumps({'best_checkpoint': '/remote/035.ckpt'}))
    for city in ['ussfo', 'usbos']:
        (parent / f'config-{city}.json').write_text(json.dumps({'dataset': city}))
        directory = tmp_path / 'chips' / city
        directory.mkdir(parents=True)
        pd.DataFrame({'chip_id': ['a', 'b', 'c'],
                      'split': ['train', 'validation', 'test']}).to_parquet(directory / 'chips.parquet')
    prepare(tmp_path, 'inference', 'source')
    frozen = tmp_path / 'run-inputs/inference/inference.json'
    manifest = json.loads(frozen.read_text())
    assert all(data['chip_ids'] == ['a'] for data in manifest['cities'].values())
    assert manifest['split'] == 'train'
    assert (tmp_path / 'inference-inputs.tar.gz').exists()
    with pytest.raises(FileExistsError):
        prepare(tmp_path, 'inference', 'source')
    (parent / 'evaluation/train-ussfo').mkdir(parents=True)
    with pytest.raises(AssertionError, match='overwrite'):
        prepare(tmp_path, 'another', 'source')
