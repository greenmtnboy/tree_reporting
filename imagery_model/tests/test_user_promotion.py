import json

import pytest

from urban_tree_ml.checkpoint_selection import resolve_parent_checkpoint, sha256


def test_explicit_promotion_preserves_rejected_result_and_binds_hashes(tmp_path):
    run = tmp_path / 'v20'; (run / 'checkpoints').mkdir(parents=True)
    result = run / 'training-result.json'
    result.write_text(json.dumps({'best_checkpoint': 'parent.ckpt',
        'checkpoint_selection': {'eligible_for_parent_advance': False}}))
    (run / 'COMPLETE').touch()
    checkpoint = run / 'checkpoints/candidate.ckpt'; checkpoint.write_bytes(b'trained')
    original = result.read_bytes()
    with pytest.raises(ValueError, match='not eligible'):
        resolve_parent_checkpoint(run)
    promotion = dict(policy='explicit_user_checkpoint_promotion_v1',run=run.name,
        training_result_sha256=sha256(result), checkpoint=checkpoint.name,
        checkpoint_sha256=sha256(checkpoint),authorization='User selected candidate',reason='Reviewed tradeoff')
    path = run / 'user-promotion.json'; path.write_text(json.dumps(promotion))
    assert resolve_parent_checkpoint(run) == checkpoint
    assert result.read_bytes() == original
    checkpoint.write_bytes(b'changed')
    with pytest.raises(ValueError, match='hash mismatch'): resolve_parent_checkpoint(run)
    checkpoint.write_bytes(b'trained')
    result.write_text('{}')
    with pytest.raises(ValueError, match='provenance'): resolve_parent_checkpoint(run)
    result.write_bytes(original)
    promotion['checkpoint'] = '../candidate.ckpt'; path.write_text(json.dumps(promotion))
    with pytest.raises(ValueError, match='filename'): resolve_parent_checkpoint(run)
    (run / 'COMPLETE').unlink()
    with pytest.raises(ValueError, match='not complete'): resolve_parent_checkpoint(run)
