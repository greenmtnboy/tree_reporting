import pandas as pd

from urban_tree_ml.regression_analysis import tree_diagnostics


def test_regression_reasons_and_improvements():
    truth = pd.DataFrame({'chip_id': list('abcde'), 'output_x': [0]*5, 'output_y': [0]*5})
    old = pd.DataFrame({'chip_id': list('abcd'), 'output_x': [0]*4, 'output_y': [0]*4, 'score': [.8]*4})
    new = pd.DataFrame({'chip_id': list('abcde'), 'output_x': [0, 0, 6, 20, 0],
                        'output_y': [0]*5, 'score': [.25, .15, .8, .8, .8]})
    result = tree_diagnostics(old, new, truth, metres=1)
    assert result.diagnosis.tolist() == ['recovered_at_020', 'recovered_at_010', 'recovered_at_8m', 'not_recovered', '']
    assert result.outcome.tolist() == ['regression']*4 + ['improvement']


def test_nearby_prediction_is_not_two_matches():
    truth = pd.DataFrame({'chip_id': ['a', 'a'], 'output_x': [0, 2], 'output_y': [0, 0]})
    old = pd.DataFrame({'chip_id': ['a', 'a'], 'output_x': [0, 2], 'output_y': [0, 0], 'score': [.9, .8]})
    new = old.iloc[:1].copy()
    result = tree_diagnostics(old, new, truth, metres=1)
    assert result.new_match.sum() == 1
    assert result.loc[1, 'diagnosis'] == 'not_recovered'
    assert result.loc[1, 'new_max_score_within_4m'] == .9
