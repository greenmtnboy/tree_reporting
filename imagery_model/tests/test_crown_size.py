import numpy as np
import pytest
from urban_tree_ml.targets import PointLabel, build_targets
from urban_tree_ml.crown_size import validate_warm_start_keys
from urban_tree_ml.crown_size import allometric_crown_radius_m, assign_crown_labels, crown_metrics
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


def test_weak_label_past_the_genus_range_grows_at_the_tail_rate():
    """A 114 in white willow (bos-54663): Tallo's Salix fit stops at 33 cm, and
    clamping there labelled it with a 3 m radius."""
    frame=pd.DataFrame([dict(tree_id='willow', split='train', species='Salix alba', dbh_eligible=True,
        dbh_log1p=np.log1p(114), crown_source='', crown_radius_m=np.nan)])
    result=assign_crown_labels(frame,[],dict(species=['Salix alba'],genera=['Salix']))
    at_max=.418346*33**.566406
    assert result.iloc[0].crown_radius_m==pytest.approx(at_max*(114*2.54/33)**.646075, rel=1e-5)
    assert result.iloc[0].crown_weight==.2


def test_allometric_radius_is_continuous_and_bounded():
    fit=dict(scale='.418346',b='.566406',dbh_max_cm='33',tail_b='.646075',tail_dbh_max_cm='385')
    at_max=.418346*33**.566406
    assert allometric_crown_radius_m(fit,10.)==pytest.approx(.418346*10**.566406)
    assert allometric_crown_radius_m(fit,33.)==pytest.approx(at_max)
    assert allometric_crown_radius_m(fit,10_000.)==pytest.approx(at_max*(385/33)**.646075)
    # A row without tail columns is its own tail: the plain clamp.
    plain={k:v for k,v in fit.items() if not k.startswith('tail')}
    assert allometric_crown_radius_m(plain,10_000.)==pytest.approx(at_max)


def test_packaged_coefficients_match_the_shared_table():
    """Training reads a packaged copy; crown_allometry_fit.py --write rewrites
    it with the shared CSV, and a hand edit to either fails here."""
    import csv
    from pathlib import Path
    from urban_tree_ml.crown_size import COEFFICIENTS
    shared=Path(__file__).resolve().parents[2]/'data'/'raw'/'crown_width_coefficients.csv'
    if not shared.exists():
        pytest.skip('data/raw is not checked out beside the imagery model')
    with COEFFICIENTS.open(encoding='utf-8',newline='') as handle:
        packaged=list(csv.DictReader(handle))
    with shared.open(encoding='utf-8',newline='') as handle:
        full=list(csv.DictReader(handle))
    assert packaged==[{k:r[k] for k in packaged[0]} for r in full]
    assert {'tail_b','tail_dbh_max_cm'}<=set(packaged[0])
