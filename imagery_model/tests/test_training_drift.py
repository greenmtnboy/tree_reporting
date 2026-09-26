import numpy as np
import pandas as pd

from urban_tree_ml.training_drift import offset_candidates


def test_offsets_are_large_confident_bounded_and_mutual():
    truth = pd.DataFrame(
        {
            "tree_id": ["large", "small", "nearby"],
            "output_x": [0.0, 40.0, 1.0],
            "output_y": [0.0, 0.0, 0.0],
            "dbh_log1p": np.log1p([20.0, 2.0, 20.0]),
        }
    )
    pred = pd.DataFrame({"output_x": [5.0, 45.0], "output_y": [0.0, 0.0], "score": [0.8, 0.8]})
    result = offset_candidates(pred, truth, 1.0)
    assert len(result) == 1
    assert result[0]["tree_id"] == "nearby"
    assert result[0]["distance_m"] == 4.0
    assert offset_candidates(pred.assign(score=0.1), truth, 1.0) == []
    assert offset_candidates(pred, truth, 10.0) == []
    assert offset_candidates(pred, truth, 0.1) == []


def test_empty_predictions_are_not_invented_offsets():
    truth = pd.DataFrame({"dbh_log1p": [3.0]})
    assert offset_candidates(pd.DataFrame({"score": []}), truth, 1.0) == []


def test_queue_requires_complete_inference_and_preserves_previous(tmp_path):
    import json
    from urban_tree_ml.training_queue import load_queue

    queues = tmp_path / 'review-queues'
    queues.mkdir()
    old = queues / 'training-curation-20260907.json'
    old.write_text(json.dumps({'cities': {'ussfo': {'dataset': 'old', 'items': []}}}))
    original = old.read_bytes()
    name = 'sf-boston-train-offset-audit-v1'
    (queues / (name + '.json')).write_text(json.dumps({
        'inference_run': name,
        'cities': {'ussfo': {'dataset': 'new', 'items': [{'chip_id': 'a'}]}}}))
    assert load_queue(tmp_path, 'ussfo')['dataset'] == 'old'
    run = tmp_path / 'runs' / name
    run.mkdir(parents=True)
    (run / 'COMPLETE').touch()
    assert load_queue(tmp_path, 'ussfo')['dataset'] == 'new'
    assert old.read_bytes() == original
