import numpy as np
import pandas as pd
from urban_tree_ml.crown_center_score import crown_matches, tolerance
from urban_tree_ml.targets import PointLabel, build_targets, center_sigma


def test_size_scaling_bounds_units_and_estimated_strength():
    assert center_sigma(PointLabel(0,0),1.5,1.2)==1.5
    assert center_sigma(PointLabel(0,0,crown_radius_m=1),1.5,1.2)==1.5
    assert center_sigma(PointLabel(0,0,crown_radius_m=100),1.5,1.2)==2.5
    assert center_sigma(PointLabel(0,0,crown_radius_m=100,crown_weight=.2),1.5,1.2)==2
    assert center_sigma(PointLabel(0,0,crown_radius_m=float('nan')),1.5,1.2)==1.5


def test_only_heatmap_changes_and_peak_remains_single():
    kwargs=dict(stride=2,gaussian_sigma_px=1.5,supervision_radius_px=8,
                background_mode='all',resolution_m=.6)
    label=PointLabel(32,32,crown_radius_m=12)
    old=build_targets(64,64,[label],**kwargs)
    new=build_targets(64,64,[label],crown_scaled_center=True,**kwargs)
    assert new['center'][16,16]==1
    assert (new['center']==1).sum()==1
    assert new['center'][16,18]>old['center'][16,18]
    for key in old:
        if key!='center':
            assert np.array_equal(old[key],new[key])


def test_small_crowns_and_collisions_do_not_change():
    kwargs=dict(stride=2,gaussian_sigma_px=1.5,supervision_radius_px=8,
                background_mode='all',resolution_m=.6)
    for labels in [[PointLabel(32,32,crown_radius_m=1)],
                   [PointLabel(32,32,crown_radius_m=12),PointLabel(32,32,crown_radius_m=10)]]:
        a=build_targets(64,64,labels,**kwargs)
        b=build_targets(64,64,labels,crown_scaled_center=True,**kwargs)
        for key in a: assert np.array_equal(a[key],b[key])


def test_size_matching_one_to_one_and_predicted_radius_cannot_cheat():
    truth=pd.DataFrame({'chip_id':['a','a','b'],'output_x':[0,10,0],
                        'output_y':[0,0,0],'crown_radius_m':[10,1,10]})
    pred=pd.DataFrame({'chip_id':['a','a','a'],'output_x':[3,3,13],
                      'output_y':[0,0,0],'score':[.9,.8,.7], 'crown_radius_m':[100]*3})
    assert crown_matches(pred,truth,1)==[(0,0,3.)]
    assert tolerance(None)==2 and tolerance(float('nan'))==2
    assert tolerance(100)==4
