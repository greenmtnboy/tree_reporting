"""Paired clean backbone POC on one immutable prepared dataset."""
import argparse
import gc
import hashlib
import json
import os
import shutil
from pathlib import Path

from urban_tree_ml.config import ProjectConfig

BACKBONES = ('convnext_tiny', 'swin_tiny')


def clean_config(config, name, backbone):
    result = config.model_copy(deep=True)
    result.experiment = name
    result.model.backbone = backbone
    result.model.pretrained = True
    result.training.epochs = 40
    result.training.learning_rate = 0.0003
    result.training.weight_decay = 0.0001
    result.training.batch_size = 8
    result.training.accumulate_grad_batches = 4
    return result


def gpu_contract(backbone):
    import torch
    from urban_tree_ml.model import RawImageryTreeModel
    from urban_tree_ml.losses import multitask_loss
    from urban_tree_ml.training import require_finite_losses
    torch.manual_seed(42)
    model = RawImageryTreeModel(input_channels=4,feature_channels=128,genus_classes=8,
        species_classes=16,pretrained=True,crown_head=True,backbone=backbone).cuda().train()
    with torch.autocast('cuda',dtype=torch.bfloat16):
        prediction = model(torch.randn(2,4,256,256,device='cuda'))
        assert prediction['center_logits'].shape == (2,128,128)
        zero=torch.zeros((2,128,128),device='cuda'); mask=zero.clone();mask[:,30,30]=1
        batch=dict(center=mask,detection_mask=torch.ones_like(zero),dbh=zero,dbh_mask=mask.bool(),
            genus=zero.long(),species=zero.long(),genus_mask=mask.bool(),species_mask=mask.bool(),
            crown=zero+1,crown_mask=mask)
        losses=multitask_loss(prediction,batch,center_weight=1,dbh_weight=.5,genus_weight=.25,species_weight=1)
    require_finite_losses(losses,backbone+' startup')
    losses['loss'].backward()
    assert all(torch.isfinite(p.grad).all() for p in model.parameters() if p.grad is not None)
    model.load_state_dict(model.state_dict(),strict=True)
    print(backbone+' pretrained RGBN forward/backward and checkpoint contract passed',flush=True)
    del model,prediction,losses,batch
    gc.collect();torch.cuda.empty_cache()


def run(root, experiment, prepared_run):
    from urban_tree_ml.joint import train_and_evaluate
    import pandas as pd
    import torch
    if not torch.cuda.is_available():
        raise RuntimeError('GPU required; do not run this POC on the reviewer workstation')
    for name in (experiment,prepared_run):
        if not name or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-' for c in name):
            raise ValueError('Invalid experiment name')
    parent=root/'runs'/prepared_run
    if not (parent/'COMPLETE').exists():
        raise ValueError('Source run must be complete')
    output=root/'runs'/experiment
    output.mkdir(parents=True,exist_ok=False)
    configs=[ProjectConfig.model_validate_json(p.read_text()) for p in sorted(parent.glob('config-*.json'))]
    if {c.inventory.city.lower() for c in configs}!={'ussfo','usbos'} or len(configs)!=2:
        raise ValueError('Both city configs required')
    fingerprints={}
    for dataset in [prepared_run,*[c.dataset for c in configs]]:
        path=root/'chips'/dataset/'chips.parquet'
        frame=pd.read_parquet(path)
        if not all((path.parent/p).is_file() for p in frame.loc[frame.split.isin(['train','validation']),'path']):
            raise ValueError('Missing prepared train/validation pixels')
        fingerprints[dataset]=hashlib.sha256(path.read_bytes()).hexdigest()
    manifest={'prepared_run':prepared_run,'chip_manifest_sha256':fingerprints,'runs':[],
              'initialization':'ImageNet pretrained backbone, fresh heads and optimizer',
              'test_evaluated':False,'training_inference':False}
    (output/'experiment.json').write_text(json.dumps(manifest,indent=2))
    # A real old checkpoint must still load strictly after the adapter refactor.
    from urban_tree_ml.model import RawImageryTreeModel
    result=json.loads((parent/'training-result.json').read_text())
    checkpoint=parent/'checkpoints'/Path(result['best_checkpoint']).name
    saved=torch.load(checkpoint,map_location='cpu',weights_only=False)
    taxonomy=json.loads(configs[0].reference.taxonomy_path.read_text())
    legacy=RawImageryTreeModel(input_channels=4,feature_channels=configs[0].model.feature_channels,
        genus_classes=len(taxonomy['genera']),species_classes=len(taxonomy['species']),
        pretrained=False,crown_head=configs[0].model.crown_head)
    legacy.load_state_dict({k.removeprefix('network.'):v for k,v in saved['state_dict'].items()
                           if k.startswith('network.')},strict=True)
    del legacy,saved
    print('Legacy checkpoint strict-load compatibility passed',flush=True)
    # Fail before either expensive run if an adapter fails its CUDA contract.
    for backbone in BACKBONES:gpu_contract(backbone)
    for backbone in BACKBONES:
        name=experiment+'-'+backbone.replace('_','-')
        child=root/'runs'/name;child.mkdir(exist_ok=False)
        inputs=root/'run-inputs'/name
        shutil.copytree(root/'run-inputs'/prepared_run,inputs,
                        ignore=shutil.ignore_patterns('warm-start.json'))
        if (inputs/'warm-start.json').exists():raise ValueError('Clean experiment cannot warm start')
        shutil.copy2(parent/'curation-audit.json',child/'curation-audit.json')
        selected=[clean_config(c,name,backbone) for c in configs]
        for config in selected:
            if config.paths.root!=root:raise ValueError('Root mismatch')
            (child/f'config-{config.inventory.city.lower()}.json').write_text(config.model_dump_json(indent=2))
        manifest['runs'].append(name)
        (output/'experiment.json').write_text(json.dumps(manifest,indent=2))
        print('Starting clean backbone run: '+name,flush=True)
        train_and_evaluate(selected,prepared_run,child,training_inference=False)
        gc.collect();torch.cuda.empty_cache()
    (output/'COMPLETE').write_text('Both clean backbone runs and validation evaluations completed.\n')


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--experiment',required=True)
    parser.add_argument('--prepared-run',required=True)
    parser.add_argument('configs',nargs='*')
    args=parser.parse_args()
    run(Path(os.environ['TREE_ML_DATA_ROOT']),args.experiment,args.prepared_run)
