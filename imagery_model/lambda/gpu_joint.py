"""Check CUDA in the actual joint process, then dispatch the unchanged pipeline."""
import runpy
import subprocess

import torch

subprocess.run(['nvidia-smi'], check=True)
assert torch.cuda.is_available(), 'CUDA unavailable in actual joint process'
probe = torch.ones(32, device='cuda')
assert probe.sum().item() == 32
del probe
torch.cuda.synchronize()
print('Actual joint process CUDA verified:', torch.cuda.get_device_name(), torch.__version__, flush=True)
from urban_tree_ml.losses import masked_centernet_focal_loss
from urban_tree_ml.training import require_finite_losses

for dtype in (torch.float32, torch.bfloat16, torch.float16):
    for masked in (False, True):
        logits = torch.tensor([-1000., -8., 0., 8., 1000.], device='cuda',
                              dtype=dtype, requires_grad=True)
        target = torch.tensor([1., 0., .5, 0., 1.], device='cuda')
        mask = torch.zeros_like(target) if masked else torch.ones_like(target)
        with torch.autocast('cuda', dtype=torch.bfloat16):
            loss = masked_centernet_focal_loss(logits, target, mask)
        require_finite_losses({'center': loss}, 'startup regression')
        loss.backward()
        assert torch.isfinite(logits.grad).all()
        if masked:
            assert loss.item() == 0 and logits.grad.eq(0).all()
try:
    require_finite_losses({'center': torch.tensor(float('inf'))}, 'startup regression')
except FloatingPointError:
    pass
else:
    raise RuntimeError('Nonfinite loss guard did not fail')
print('CUDA focal-loss regression checks passed (fp32/bf16/fp16, masked, gradients).', flush=True)
from urban_tree_ml.model import RawImageryTreeModel
from urban_tree_ml.losses import multitask_loss
model = RawImageryTreeModel(input_channels=4, feature_channels=32, genus_classes=2,
                           species_classes=2, pretrained=False, crown_head=True).cuda()
image = torch.randn(2,4,64,64,device='cuda')
with torch.autocast('cuda', dtype=torch.bfloat16):
    prediction = model(image)
    shape = prediction['center_logits'].shape
    zero = torch.zeros(shape, device='cuda')
    mask = zero.clone(); mask[:,10,10] = .2; mask[:,20,20] = 1
    batch = dict(center=zero,detection_mask=torch.ones_like(zero),dbh=zero,dbh_mask=zero.bool(),
                 genus=zero.long(),species=zero.long(),genus_mask=zero.bool(),species_mask=zero.bool(),
                 crown=torch.ones_like(zero),crown_mask=mask)
    losses = multitask_loss(prediction,batch,center_weight=1,dbh_weight=1,genus_weight=1,species_weight=1)
require_finite_losses(losses,'crown startup regression')
losses['loss'].backward()
assert model.crown_head[-1].weight.grad is not None
assert torch.isfinite(model.crown_head[-1].weight.grad).all()
assert losses['crown_loss'] > 0
del model,image,prediction,batch,losses
torch.cuda.empty_cache()
print('Crown head CUDA forward/backward and weighted supervision passed.',flush=True)
runpy.run_module('urban_tree_ml.joint', run_name='__main__')
