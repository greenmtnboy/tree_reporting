import hashlib
import json
from types import SimpleNamespace

import pytest

from urban_tree_ml import expand_review_random as expansion


def test_apply_preserves_annotations_saved_after_staging(tmp_path, monkeypatch):
    live, stage = tmp_path / 'live', tmp_path / 'stage'
    for directory in [live, stage]:
        (directory / 'images').mkdir(parents=True)
    original = b'{"scenes": []}'
    (live / 'manifest.json').write_bytes(original)
    latest = b'{"tree_reviews": {"recent": {"status": "offset"}}}'
    (live / 'reviews.json').write_bytes(latest)
    (stage / 'manifest.json').write_text('{"scenes": [{"scene_id": "new"}]}')
    (stage / 'report.json').write_text(json.dumps({'before_sha256': hashlib.sha256(original).hexdigest()}))
    (stage / 'images' / 'new.png').write_bytes(b'image')
    monkeypatch.setattr(expansion, 'snapshot_registration_annotations', lambda *a, **k: None)
    expansion.apply(SimpleNamespace(imagery=SimpleNamespace(local_raster='fixture')), live, stage)
    assert (live / 'reviews.json').read_bytes() == latest
    assert (stage / 'before-apply' / 'reviews.json').read_bytes() == latest
    assert (live / 'images' / 'new.png').read_bytes() == b'image'


def test_apply_refuses_concurrent_manifest_change(tmp_path):
    (tmp_path / 'manifest.json').write_text('{}')
    (tmp_path / 'report.json').write_text('{"before_sha256": "old"}')
    with pytest.raises(ValueError, match='collection changed'):
        expansion.apply(None, tmp_path, tmp_path)
    assert not (tmp_path / 'before-apply').exists()
