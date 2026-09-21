import pytest

from urban_tree_ml.center_pilot import human_point, metric_distance, validate_response


def test_center_response_rejects_missing_duplicate_and_invalid_coordinates():
    inputs = {'width':256,'height':256,'points':[{'id':'p1'}]}
    valid = {'id':'p1','status':'visible','x':10,'y':20,'confidence':.8}
    assert validate_response({'trees':[valid]}, inputs) == [valid]
    for rows in [[],[valid,valid],[{**valid,'id':'p2'}],[{**valid,'x':float('nan')}],
                 [{**valid,'x':256}],[{**valid,'confidence':True}],
                 [{**valid,'status':'uncertain'}]]:
        with pytest.raises(ValueError): validate_response({'trees':rows}, inputs)
    assert validate_response({'trees':[{**valid,'status':'uncertain','x':None,'y':None}]},inputs)


def test_center_metric_and_offsets():
    sample = {'target_x':10,'target_y':20,'transform_a':.6,'transform_b':0,
              'transform_d':0,'transform_e':-.6}
    assert human_point(sample,{'east_m':3,'north_m':6},None) == [15,10]
    assert metric_distance([10,20],[15,20],sample) == 3
