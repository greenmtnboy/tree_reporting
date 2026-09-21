import hashlib
import json

import pytest

from urban_tree_ml.feedback import _json_sha256, _review_state_sha256
from urban_tree_ml.freeze_next_run import FROZEN_FILES, validate_frozen_city


def bundle(path):
    manifest = {'metadata': {}, 'samples': []}
    state = {'reviews': {'s': {'status': 'offset', 'east_m': 1.0000000002}},
             'scene_reviews': {}, 'mask_regions': []}
    state['state_revision'] = _review_state_sha256(
        state['reviews'], state['scene_reviews'], state['mask_regions'])
    values = {'manifest.json': manifest, 'reviews.json': {'schema_version': 2},
              'published-state.json': state, 'taxonomy.json': {},
              'training-feedback.json': {'source_reviews_sha256': state['state_revision'],
                                         'source_manifest_sha256': _json_sha256(manifest)}}
    for name, value in values.items():
        (path / name).write_text(json.dumps(value))
    (path / 'inventory.parquet').write_bytes(b'inventory')
    receipt(path)
    return state


def receipt(path):
    (path / 'integrity.json').write_text(json.dumps({'schema_version': 1, 'sha256': {
        name: hashlib.sha256((path / name).read_bytes()).hexdigest() for name in FROZEN_FILES}}))


def test_validation_does_not_reproject(tmp_path, monkeypatch):
    state = bundle(tmp_path)
    def forbidden(*args, **kwargs):
        pytest.fail('Frozen validation must not recompute projected offsets')
    monkeypatch.setattr('urban_tree_ml.feedback.load_persisted_reviews', forbidden)
    assert validate_frozen_city(tmp_path) == state


@pytest.mark.parametrize('name', FROZEN_FILES)
def test_every_frozen_input_is_bound(tmp_path, name):
    bundle(tmp_path)
    with (tmp_path / name).open('ab') as stream:
        stream.write(b' ')
    with pytest.raises(ValueError, match='checksum mismatch'):
        validate_frozen_city(tmp_path)


def test_resealed_stale_publication_rejected(tmp_path):
    bundle(tmp_path)
    path = tmp_path / 'published-state.json'
    state = json.loads(path.read_text())
    state['reviews']['s']['east_m'] = 2
    path.write_text(json.dumps(state))
    receipt(tmp_path)
    with pytest.raises(ValueError, match='Unpublished'):
        validate_frozen_city(tmp_path)
