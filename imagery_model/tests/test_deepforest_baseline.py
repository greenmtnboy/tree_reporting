import numpy as np
import pandas as pd
import pytest
from urban_tree_ml.deepforest_baseline import box_records, score_at


def test_boxes_preserve_native_coordinates_and_missing_attributes():
    mask=np.ones((16,16));mask[5,4]=0
    r=box_records('chip',[[4,6,12,14]],[.8],np.zeros_like(mask),mask,2,.6)[0]
    assert (r['output_x'],r['output_y'])==(4,5)
    assert r['crown_radius_m']==pytest.approx(2.4)
    assert r['detection_mask_value']==0
    assert r['species_id'] is None and r['dbh_in'] is None
    assert r['xmin']==4 and r['xmax']==12


def test_bad_boxes_fail():
    with pytest.raises(ValueError):
        box_records('chip',[[2,2,1,3]],[.8],np.zeros((8,8)),np.ones((8,8)),2,.6)


def test_goal_scoring_ignores_unmatched_protected_predictions():
    p=pd.DataFrame(dict(chip_id=['a','a','a'],output_x=[1,10,20],output_y=[1,10,20],
                        score=[.8,.7,.6],detection_mask_value=[1,0,1]))
    t=pd.DataFrame(dict(chip_id=['a'],output_x=[1],output_y=[1]))
    result,_=score_at(p,t,1,.35,'2.0')
    assert result['true_positive']==1
    assert result['false_positive']==1
    assert result['ignored_unmatched']==1
