import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import torch

from urban_tree_ml.dataset import NpzChipDataset, apply_dihedral
from urban_tree_ml.losses import masked_centernet_focal_loss
from urban_tree_ml.targets import DetectionMaskRegion, PointLabel, build_targets
from test_checkpoint_selection import tiny_training


def targets(labels=(), regions=(), valid=None):
    return build_targets(32,32,list(labels),stride=2,gaussian_sigma_px=1,
                         supervision_radius_px=4,background_mode='all',
                         mask_regions=list(regions),valid_mask=valid)


def test_double_background_loss_doubles_only_confirmed_negative_gradients():
    target = torch.tensor([[[[0.,0.,1.,.5,0.]]]])
    valid = torch.tensor([[[[1.,1.,1.,1.,0.]]]])
    confirmed = torch.tensor([[[[1.,0.,1.,1.,1.]]]])
    baseline_logits = torch.zeros_like(target,requires_grad=True)
    weighted_logits = baseline_logits.detach().clone().requires_grad_()
    baseline = masked_centernet_focal_loss(baseline_logits,target,valid)
    weighted = masked_centernet_focal_loss(weighted_logits,target,valid,confirmed,2.)
    baseline.backward()
    weighted.backward()
    expected = baseline_logits.grad.clone()
    expected[...,0] *= 2
    torch.testing.assert_close(weighted_logits.grad,expected)
    assert weighted_logits.grad[...,0] > 0
    assert weighted_logits.grad[...,4] == 0
    assert weighted > baseline
    torch.testing.assert_close(masked_centernet_focal_loss(weighted_logits,target,valid,confirmed,1.),baseline)


@pytest.mark.parametrize('dtype',[torch.float32,torch.bfloat16])
def test_empty_background_has_finite_loss_and_suppression(dtype):
    logits = torch.full((2,1,16,16),10.,dtype=dtype,requires_grad=True)
    loss = masked_centernet_focal_loss(logits,torch.zeros_like(logits),torch.ones_like(logits),
                                      torch.ones_like(logits),2.)
    loss.backward()
    assert torch.isfinite(loss) and torch.isfinite(logits.grad).all()
    assert (logits.grad > 0).all()


def test_background_mask_respects_positive_single_tree_group_and_nodata():
    polygon = ((0,0),(32,0),(32,32),(0,32))
    background = DetectionMaskRegion(16,16,32,'confirmed-background',polygon)
    valid = np.ones((32,32),dtype=bool)
    valid[:4] = False
    positive = PointLabel(8,8)
    result = targets([positive],[background],valid)
    assert result['confirmed_background_mask'].sum() > 0
    assert not result['confirmed_background_mask'][result['center']>0].any()
    assert not result['confirmed_background_mask'][:2].any()
    for mode in ('single-tree','copse','shrub-mass','protect'):
        area = DetectionMaskRegion(16,16,32,mode,polygon)
        assert not targets([positive],[background,area])['confirmed_background_mask'].any()
    woods = DetectionMaskRegion(16,16,32,'confirmed-woods',polygon)
    np.testing.assert_array_equal(targets([], [woods,background])['confirmed_background_mask'],
                                  targets([], [background,woods])['confirmed_background_mask'])
    np.testing.assert_array_equal(result['scoring_mask'],result['detection_mask'])


def test_dataset_loads_legacy_zero_mask_and_transforms_new_mask(tmp_path):
    payload = targets(regions=[DetectionMaskRegion(8,8,4,'confirmed-background')])
    payload['image'] = np.zeros((4,32,32),dtype=np.float32)
    np.savez(tmp_path/'new.npz',**payload)
    payload.pop('confirmed_background_mask')
    np.savez(tmp_path/'old.npz',**payload)
    pd.DataFrame([dict(chip_id=name,path=name+'.npz',split='train') for name in ('new','old')]).to_parquet(tmp_path/'chips.parquet')
    (tmp_path/'normalization.json').write_text(json.dumps(dict(mean=[0]*4,std=[1]*4)))
    dataset = NpzChipDataset(tmp_path/'chips.parquet','train')
    new, old = dataset[0],dataset[1]
    assert new['confirmed_background_mask'].any()
    assert not old['confirmed_background_mask'].any()
    weighted = NpzChipDataset(tmp_path/'chips.parquet','train',require_confirmed_background_mask=True)
    assert weighted[0]['confirmed_background_mask'].any()
    with pytest.raises(ValueError,match='Rebuild chips'):
        weighted[1]
    for transform in range(8):
        changed = apply_dihedral(new,transform)
        expected = torch.rot90(new['confirmed_background_mask'],transform%4,(-2,-1))
        if transform >= 4:
            expected = torch.flip(expected,(-1,))
        torch.testing.assert_close(changed['confirmed_background_mask'],expected)


def test_weight_is_in_warm_recipe_and_baseline_signature(tmp_path):
    from urban_tree_ml.checkpoint_selection import validation_signature
    from urban_tree_ml.config import load_config
    from urban_tree_ml.warm_recipe import apply_warm_recipe, recipe_from_configs
    config = load_config(Path(__file__).parents[1]/'configs/sf_naip_smoke.yaml')
    config.paths.root = tmp_path
    (tmp_path/'chips.parquet').write_bytes(b'fixture')
    (tmp_path/'normalization.json').write_text('{}')
    class EmptyDataset:
        root = tmp_path
        rows = pd.DataFrame(columns=['path'])
        def __len__(self): return 0
    before = validation_signature(EmptyDataset(),config,{})
    apply_warm_recipe(config,{'confirmed_background_loss_weight':2.})
    assert recipe_from_configs([config])['confirmed_background_loss_weight'] == 2.
    after = validation_signature(EmptyDataset(),config,{})
    assert before['sha256'] != after['sha256']
    assert after['loss_policy']['confirmed_background_loss_weight'] == 2.


def test_real_trainer_uses_same_background_weight_for_parent_and_training(tiny_training, monkeypatch):
    config, parent, losses, training, _ = tiny_training
    config.training.confirmed_background_loss_weight = 2.
    stages = set()
    def loss(output, batch, **weights):
        assert weights['confirmed_background_weight'] == 2.
        validation = bool(batch['is_validation'][0])
        stages.add('validation' if validation else 'train')
        return {'loss':4. + (output-1.) if validation else output}
    monkeypatch.setattr(losses,'multitask_loss',loss)
    result = training.run_training(config,initial_checkpoint=str(parent))
    baseline = json.loads((Path(result['run_dir'])/'parent-baseline.json').read_text())
    assert stages == {'train','validation'}
    assert baseline['global_step'] == 0
    assert baseline['validation_inputs']['loss_policy']['confirmed_background_loss_weight'] == 2.
