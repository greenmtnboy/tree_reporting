import hashlib
import json

import pandas as pd

from urban_tree_ml.curated_only import completed_chips, pin_validation, restrict_joint


def test_only_completed_full_training_scenes():
    manifest = {'scenes': [
        {'scene_id': 'a', 'validation_chip_id': 'a', 'splits': ['train']},
        {'scene_id': 'b', 'validation_chip_id': 'b', 'splits': ['train']},
        {'scene_id': 'small', 'splits': ['train']},
        {'scene_id': 'test', 'validation_chip_id': 'test', 'splits': ['test']},
        {'scene_id': 'pending', 'validation_chip_id': 'pending', 'splits': ['train']},
    ]}
    state = {'scene_reviews': {'a': {'done': True}, 'b': {'more_done': True},
                              'small': {'done': True}, 'test': {'done': True}}}
    assert completed_chips(manifest, state) == ['a', 'b']


def test_pinned_validation_and_training_subset(tmp_path):
    old = tmp_path / 'chips/old'
    new = tmp_path / 'chips/new'
    old.mkdir(parents=True)
    new.mkdir()
    (old / 'val.npz').write_bytes(b'pixels')
    original = pd.DataFrame({'chip_id': ['a', 'v', 't'], 'split': ['train','validation','test'],
                             'path': ['a.npz', 'val.npz', 't.npz'], 'tree_count': [1,2,3]})
    fresh = original.copy()
    fresh['tree_count'] = [4,5,6]
    hashes = {}
    for name in ['chips.parquet', 'labels.parquet']:
        original.to_parquet(old / name, index=False)
        fresh.to_parquet(new / name, index=False)
        hashes[name] = hashlib.sha256((old / name).read_bytes()).hexdigest()
    policy = {'cities': {'ussfo': {'baseline_dataset': 'old',
                                   'validation_sha256': hashes, 'chip_ids': ['a']}}}
    pin_validation(tmp_path, {'ussfo': new}, policy)
    result = pd.read_parquet(new / 'chips.parquet')
    assert result.set_index('chip_id').tree_count.to_dict() == {'a':4, 'v':2, 't':6}
    assert result.loc[result.chip_id == 'v', 'path'].iloc[0] == '../old/val.npz'
    joint = tmp_path / 'chips/run'
    joint.mkdir()
    (tmp_path / 'runs/run').mkdir(parents=True)
    result['source_chip_id'] = result.chip_id
    result['city'] = 'ussfo'
    result['chip_id'] = 'ussfo:' + result.chip_id
    extra = result.iloc[:1].copy()
    extra['chip_id'] = 'ussfo:unreviewed'
    pd.concat([result, extra]).to_parquet(joint / 'chips.parquet', index=False)
    restrict_joint(tmp_path, 'run', policy)
    filtered = pd.read_parquet(joint / 'chips.parquet')
    assert set(filtered.chip_id) == {'ussfo:a', 'ussfo:v', 'ussfo:t'}
    assert json.loads((tmp_path/'runs/run/curated-only-audit.json').read_text())['actual_training']['ussfo']['targets'] == 4
