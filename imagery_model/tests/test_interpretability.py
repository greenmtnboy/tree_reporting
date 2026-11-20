import pytest
import torch
from torch import nn

from urban_tree_ml.interpretability import inspect_center_head, sample_points


class TinyDetector(nn.Module):
    def __init__(self):
        super().__init__()
        self.center_head = nn.Sequential(nn.ReLU(), nn.Conv2d(3, 1, 1))
        with torch.no_grad():
            self.center_head[-1].weight.copy_(torch.tensor([2., -3., .5]).reshape(1,3,1,1))
            self.center_head[-1].bias.fill_(-1.)

    def forward(self, x):
        return {'center_logits': self.center_head(x).squeeze(1)}


def test_signed_contributions_and_zeroing_are_exact_and_do_not_mutate():
    model=TinyDetector().eval()
    x=torch.tensor([1.,2.,4.]).reshape(1,3,1,1).expand(1,3,4,4).clone()
    before={k:v.clone() for k,v in model.state_dict().items()}
    result=inspect_center_head(model,x)
    assert result['contributions'][0,:,0,0].tolist()==[2.,-6.,2.]
    assert result['logits'][0,0,0]==-3
    assert result['ablation_checked_channel']==1
    assert result['ablation_max_error']==0
    assert result['reconstruction_max_error']==0
    assert not model.center_head[-1]._forward_pre_hooks
    assert all(torch.equal(v,before[k]) for k,v in model.state_dict().items())
    assert torch.equal(x[:,1],torch.full_like(x[:,1],2.))


def test_training_and_wrong_readout_fail_closed():
    model=TinyDetector()
    with pytest.raises(ValueError,match='eval'):inspect_center_head(model,torch.ones(1,3,4,4))
    model.eval();model.center_head[-1]=nn.Conv2d(3,1,3)
    with pytest.raises(ValueError,match='1x1'):inspect_center_head(model,torch.ones(1,3,4,4))


def test_hook_is_removed_on_forward_failure():
    model=TinyDetector().eval()
    with pytest.raises(RuntimeError):inspect_center_head(model,torch.ones(1,5,4,4))
    assert not model.center_head[-1]._forward_pre_hooks


def test_peak_sampling_preserves_strongest_and_production_tie_breaking():
    logits=torch.full((12,12),-10.)
    logits[2:4,2:4]=3.;logits[9,9]=2.
    points=sample_points(logits,kernel=5,limit=2)
    assert points.tolist()==[[2,2],[9,9]]
