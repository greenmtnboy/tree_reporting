import json
import pandas as pd
import pytest
from urban_tree_ml.goal_metrics import goal_metrics, ignored_ids
from urban_tree_ml.model_debug import RUN_HISTORY_HTML


def fixture(tmp_path, masks=True):
    metrics={'split':'validation','confidence_threshold':.35,'metrics_by_match_radius_m':{
        '4.0':{'detection':{'true_positive':1,'false_negative':1}}}}
    (tmp_path/'metrics.json').write_text(json.dumps(metrics))
    data={'score':[.9,.8,.7,.1]}
    if masks:
        data['detection_mask_value']=[0.,0.,1.,1.]
    pd.DataFrame(data).to_parquet(tmp_path/'predictions.parquet')
    pd.DataFrame({'radius_m':[4.], 'prediction_index':[0]}).to_parquet(tmp_path/'matches.parquet')


def test_matches_are_credited_before_mask_ignores_and_cache_is_defensive(tmp_path):
    fixture(tmp_path)
    value=goal_metrics(tmp_path)['4.0']['detection']
    assert value['true_positive']==1
    assert value['false_positive']==1
    assert value['ignored_unmatched']==1
    assert value['f2']==.5
    assert value['average_precision'] is None
    value['f2']=99
    assert goal_metrics(tmp_path)['4.0']['detection']['f2']==.5


def test_legacy_missing_masks_do_not_fall_back_to_inventory(tmp_path):
    fixture(tmp_path,False)
    assert goal_metrics(tmp_path) is None


def test_unknown_mask_is_not_credited_or_silently_penalized():
    assert ignored_ids(pd.DataFrame({'detection_mask_value':[float('nan')]}),set()) is None


def test_history_bests_and_chart_use_goal_metrics():
    assert "run.goal_metrics?.[$('radius').value]?.detection||{}" in RUN_HISTORY_HTML
    assert "['Goal-aligned F2'" in RUN_HISTORY_HTML
    assert 'highest goal-aligned F2' in RUN_HISTORY_HTML
