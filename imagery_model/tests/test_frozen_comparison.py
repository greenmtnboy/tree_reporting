import pandas as pd
import pytest

from urban_tree_ml.evaluation import _average_precision, _greedy_matches
from urban_tree_ml.frozen_comparison import (
    average_precision,
    counts,
    match_frame,
    select_targets,
    write_csv,
)


def test_csv_export(tmp_path):
    import csv

    path = tmp_path / "targets.csv"
    write_csv(pd.DataFrame({"chip": ["a"], "reason": ["trees, uncertain"]}), path)
    with path.open(newline="") as handle:
        assert list(csv.DictReader(handle)) == [{"chip": "a", "reason": "trees, uncertain"}]


def test_matches_agree_with_evaluator_and_threshold_prefix():
    truth = pd.DataFrame({"chip_id": ["a", "a", "b"], "output_x": [0, 3, 0], "output_y": [0, 0, 0]})
    pred = pd.DataFrame(
        {
            "chip_id": ["a", "a", "a", "b", "empty"],
            "output_x": [1, 0, 3, 0, 0],
            "output_y": [0] * 5,
            "score": [0.9, 0.8, 0.2, 0.7, 0.6],
        }
    )
    matched = match_frame(pred, truth, 1.5)
    pairs = [
        (int(r.Index), int(r.truth_index), r.distance_px)
        for r in matched.itertuples()
        if r.truth_index >= 0
    ]
    assert pairs == _greedy_matches(pred, truth, radius_output_px=1.5)
    selected = matched[matched.score >= 0.35]
    expected = _greedy_matches(pred[pred.score >= 0.35], truth, radius_output_px=1.5)
    assert int((selected.truth_index >= 0).sum()) == len(expected)
    assert average_precision(matched, len(truth)) == pytest.approx(
        _average_precision(pred, truth, radius_output_px=1.5)
    )
    assert counts(selected, 3)["f2"] == pytest.approx(10 / 16)


def test_empty_truth_and_predictions():
    pred = pd.DataFrame(columns=["chip_id", "output_x", "output_y", "score"])
    truth = pd.DataFrame(columns=["chip_id", "output_x", "output_y"])
    matched = match_frame(pred, truth, 2)
    assert counts(matched, 0)["recall"] is None
    assert average_precision(matched, 0) is None


def test_queue_is_unique_and_reproducible():
    chips = pd.DataFrame(
        {
            "chip_id": [f"chip-{i}" for i in range(100)],
            "truth_count": list(range(100)),
            "new_missed": list(range(100)),
            "new_unmatched_predictions": [2] * 100,
            "old_f2": [0.4] * 100,
            "new_f2": [0.2] * 100,
            "reviewed_now": [False] * 100,
        }
    )
    first = select_targets(chips)
    pd.testing.assert_frame_equal(first, select_targets(chips))
    assert len(first) == first.chip_id.nunique() == 50
    assert first.queue.value_counts().to_dict() == {"representative audit": 25, "diagnostic": 25}
