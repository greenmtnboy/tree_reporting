import numpy as np
import pytest
from urban_tree_ml.targets import PointLabel, build_targets
from urban_tree_ml.crown_size import validate_warm_start_keys
from urban_tree_ml.crown_size import assign_crown_labels, crown_metrics
import pandas as pd


def test_measured_crown_targets_unknown_and_collision():
    result = build_targets(32, 32, [PointLabel(4,4,crown_radius_m=3), PointLabel(12,12),
        PointLabel(20,20,crown_radius_m=4), PointLabel(20,20,crown_radius_m=5)],
        stride=2, gaussian_sigma_px=1, supervision_radius_px=2, background_mode='all')
    assert result['crown_mask'].sum() == 1
    assert result['crown'][2,2] == pytest.approx(np.log1p(3))
    assert result['dbh_mask'].sum() == 0


def test_only_complete_crown_head_migration_is_allowed():
    expected={'network.center.weight','network.crown_head.weight','network.crown_head.bias'}
    validate_warm_start_keys(expected, {'network.center.weight'})
    validate_warm_start_keys(expected, expected)
    with pytest.raises(ValueError):
        validate_warm_start_keys(expected, {'network.center.weight','network.crown_head.bias'})
    with pytest.raises(ValueError):
        validate_warm_start_keys(expected, {'unrelated'})


def test_weak_estimates_human_override_and_species():
    frame=pd.DataFrame([dict(tree_id=t, split='train', species='Acer rubrum', dbh_eligible=True,
        dbh_log1p=np.log1p(20), crown_source='human' if t.startswith('manual') else '',
        crown_radius_m=2 if t.startswith('manual') else np.nan) for t in ['estimate','override','manual-1']])
    regions=[dict(tree_id='override',mode='confirmed-tree',splits=['train'],radius_m=7)]
    result=assign_crown_labels(frame,regions,dict(species=['Acer rubrum'],genera=['Acer']))
    assert result.iloc[0].crown_radius_m>0 and result.iloc[0].crown_weight==.2
    assert result.iloc[1].crown_radius_m==7 and result.iloc[1].crown_weight==1
    assert result.iloc[2].species_id==0 and result.iloc[2].species_eligible
    assert result.iloc[2].crown_radius_m==2
    pred=pd.DataFrame({'crown_radius_m':[100,8,3]})
    metrics=crown_metrics(pred,result,[(0,0,0),(1,1,0),(2,2,0)])
    assert metrics['samples']==2 and metrics['radius_mae_m']==1
