import pytest
import torch
from torch import nn
from urban_tree_ml.species_microscope import inspect_species_centers, species_summary


class ToySpecies(nn.Module):
    def __init__(self):
        super().__init__()
        self.species_head = nn.Sequential(nn.ReLU(), nn.Conv2d(3, 3, 1))
        with torch.no_grad():
            self.species_head[-1].weight.copy_(torch.tensor([[2.,-1.,.5],[-2.,3.,1.],[0.,0.,1.]]).reshape(3,3,1,1))
            self.species_head[-1].bias.copy_(torch.tensor([1.,-1.,2.]))

    def forward(self, image):
        return {'species_logits': self.species_head(image)}


def test_class_margin_and_all_class_intervention_agree_with_layer():
    model = ToySpecies().eval()
    image = torch.tensor([1.,2.,4.]).reshape(1,3,1,1).expand(1,3,3,3).clone()
    before = {k:v.clone() for k,v in model.state_dict().items()}
    r = inspect_species_centers(model,image,[0,2],[1,2])
    torch.testing.assert_close(r['logits'],torch.tensor([[3.,7.,6.],[3.,7.,6.]]))
    margin = r['features'] @ (r['weights'][1]-r['weights'][0]) + r['bias'][1]-r['bias'][0]
    torch.testing.assert_close(margin,r['logits'][:,1]-r['logits'][:,0])
    altered = r['features'].clone();altered[:,1]=0
    changed = altered @ r['weights'].T+r['bias']
    assert changed.argmax(-1).tolist() == [2,2]  # Removing a channel changes the full softmax, not just a pair.
    assert r['zeroing_error'] == 0 and r['reconstruction_error'] == 0
    assert all(torch.equal(v,before[k]) for k,v in model.state_dict().items())
    assert not model.species_head[-1]._forward_pre_hooks


def test_guard_and_failure_remove_hook():
    model=ToySpecies()
    with pytest.raises(ValueError,match='eval'):inspect_species_centers(model,torch.ones(1,3,2,2),[0],[0])
    model.eval()
    with pytest.raises(RuntimeError):inspect_species_centers(model,torch.ones(1,4,2,2),[0],[0])
    assert not model.species_head[-1]._forward_pre_hooks


def test_accuracy_denominator_keeps_errors_and_perfect_and_absent_species():
    names=['Acer rubrum','Quercus rubra','Acer saccharum','__unknown__']
    rows=[dict(truth=0,predicted=0,top5=[0,1],city='a',chip='1'),
          dict(truth=0,predicted=1,top5=[1,2],city='a',chip='1'),
          dict(truth=1,predicted=1,top5=[1,0],city='b',chip='1')]
    s=species_summary(rows,names)
    assert s==[dict(id=0,name=names[0],n=2,correct=1,top5=1,tiles=1),
               dict(id=1,name=names[1],n=1,correct=1,top5=1,tiles=1),
               dict(id=2,name=names[2],n=0,correct=0,top5=0,tiles=0)]
