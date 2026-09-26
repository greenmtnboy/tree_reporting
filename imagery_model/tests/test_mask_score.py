import pandas as pd
import pytest
from urban_tree_ml.mask_score import score_matched


def test_matches_win_and_ignored_predictions_get_no_positive_credit():
    frame = pd.DataFrame({'truth_index':[0,-1,-1,-1],
                          'detection_mask_value':[0,0,1,1],
                          'center_target':[1,0,0,.5]})
    result = score_matched(frame, 2)
    assert result['strict']['fp'] == 3
    assert result['mask_aware']['fp'] == 2
    assert result['ignored_unmatched'] == 1
    assert result['mask_aware']['tp'] == 1
    assert result['mask_aware']['recall'] == result['strict']['recall'] == .5
    assert result['target_weighted_diagnostic']['fp'] == pytest.approx(1.0625)


def test_ignored_only_does_not_mean_perfect_discovery_precision():
    frame = pd.DataFrame({'truth_index':[-1], 'detection_mask_value':[0], 'center_target':[0]})
    result = score_matched(frame, 1)
    assert result['mask_aware']['f2'] == 0
    assert result['mask_aware']['tp'] == 0
