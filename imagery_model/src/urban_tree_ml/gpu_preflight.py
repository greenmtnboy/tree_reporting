"""Reproducible, fail-closed high-resolution backward diagnostics before training."""
import hashlib
import json
from pathlib import Path

import torch

from urban_tree_ml.model import RawImageryTreeModel
from urban_tree_ml.vegetation_groups import expanded_taxonomy, migrate_group_head


def gradient_audit(model):
    bad, missing = [], []
    for name, parameter in model.named_parameters():
        if not parameter.requires_grad:
            continue
        if parameter.grad is None:
            missing.append(name)
        elif not torch.isfinite(parameter.grad).all():
            bad.append({'name': name, 'nan': int(parameter.grad.isnan().sum()),
                        'inf': int(parameter.grad.isinf().sum())})
    return {'bad_gradients': bad, 'missing_gradients': missing}


def require_passed(results):
    failed = [r['name'] for r in results if not r['passed']]
    if failed:
        raise FloatingPointError('512px preflight failed: ' + ', '.join(failed))


def probe(name, taxonomy, *, seed, mixed=True, checkpoint=None, parent=None,
          backbone='swin_tiny', pretrained=False):
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    model = RawImageryTreeModel(input_channels=4, feature_channels=128,
        genus_classes=len(taxonomy['genera']), species_classes=len(taxonomy['species']),
        pretrained=pretrained, crown_head=True, backbone=backbone)
    if checkpoint is not None:
        saved = torch.load(checkpoint, map_location='cpu', weights_only=False)['state_dict']
        if parent is not None:
            initial = {'network.' + k: v for k, v in model.state_dict().items()}
            saved = migrate_group_head(saved, initial, parent, taxonomy)
        model.load_state_dict({k.removeprefix('network.'): v for k, v in saved.items()}, strict=True)
        del saved
    model.cuda()
    # Separate input RNG: head width must not change the diagnostic image.
    generator = torch.Generator(device='cuda').manual_seed(seed + 1)
    image = torch.randn(2, 4, 512, 512, device='cuda', generator=generator)
    with torch.autocast('cuda', dtype=torch.bfloat16, enabled=mixed):
        output = model(image)
        assert output['center_logits'].shape == (2, 256, 256)
        loss = sum(value.float().square().mean() for value in output.values())
    loss.backward()
    result = dict(name=name, seed=seed, backbone=backbone, pretrained=pretrained,
        precision='bf16' if mixed else 'fp32',
        species_classes=len(taxonomy['species']), loss=float(loss.detach()),
        output_finite={k: bool(torch.isfinite(v).all()) for k, v in output.items()},
        **gradient_audit(model))
    result['passed'] = bool(torch.isfinite(loss)) and all(result['output_finite'].values()) \
        and not result['bad_gradients'] and not result['missing_gradients']
    print('512px backward diagnostic:', json.dumps(result), flush=True)
    del model, image, output, loss
    torch.cuda.empty_cache()
    return result


def run(root, experiment):
    root = Path(root)
    inputs = root / 'run-inputs' / experiment
    frozen = json.loads((inputs / 'ussfo/taxonomy.json').read_text())
    warm_path = inputs / 'warm-start.json'
    warm = json.loads(warm_path.read_text()) if warm_path.exists() else {}
    base_path = inputs/'base-start.json'
    if base_path.exists():
        from urban_tree_ml.capacity_run import validate_base_recipe
        base = json.loads(base_path.read_text())
        validate_base_recipe(base)
        if warm:
            raise ValueError('Base preflight cannot load tree-model weights')
        taxonomy = expanded_taxonomy(frozen) if base.get('vegetation_groups') else frozen
        results = []
        path = root/(experiment+'-preflight.json')
        for name, mixed, pretrained in [('synthetic-bf16', True, False),
                                         ('synthetic-fp32', False, False),
                                         ('actual-imagenet-bf16', True, True)]:
            results.append(probe(name, taxonomy, seed=20260820, mixed=mixed,
                                 backbone=base['model']['backbone'], pretrained=pretrained))
            path.write_text(json.dumps(dict(torch=torch.__version__, cuda=torch.version.cuda,
                gpu=torch.cuda.get_device_name(), initialization=base['mode'], results=results), indent=2)+'\n')
        require_passed(results)
        return
    migrate = warm.get('vegetation_groups') is True
    candidate = expanded_taxonomy(frozen) if migrate else frozen
    parent = frozen
    if warm:
        checkpoint = root / warm['checkpoint']
        parent = json.loads((checkpoint.parents[1] / 'evaluation/validation/taxonomy.json').read_text())
        from urban_tree_ml.warm_recipe import validate_warm_taxonomy
        validate_warm_taxonomy(parent, frozen, vegetation_groups=migrate)
    receipt = dict(seed=20260820, torch=torch.__version__, cuda=torch.version.cuda,
                   cudnn=torch.backends.cudnn.version(), gpu=torch.cuda.get_device_name(), results=[])
    path = root / (experiment + '-preflight.json')
    def record(result):
        receipt['results'].append(result)
        path.write_text(json.dumps(receipt, indent=2) + '\n')
    record(probe('synthetic-parent-bf16', parent, seed=receipt['seed']))
    if candidate != parent:
        record(probe('synthetic-expanded-bf16', candidate, seed=receipt['seed']))
    # A precision control is diagnostic only, never a fallback that bypasses bf16 failures.
    record(probe('synthetic-candidate-fp32', candidate, seed=receipt['seed'], mixed=False))
    if warm:
        checkpoint = root / warm['checkpoint']
        if hashlib.sha256(checkpoint.read_bytes()).hexdigest() != warm['checkpoint_sha256']:
            raise ValueError('Preflight parent checkpoint hash mismatch')
        record(probe('actual-warm-start-bf16', candidate, seed=receipt['seed'],
                     checkpoint=checkpoint, parent=parent if candidate != parent else None))
    require_passed(receipt['results'])
    print('512px Swin batch-2 deterministic backward preflight passed, including actual warm-start.', flush=True)
