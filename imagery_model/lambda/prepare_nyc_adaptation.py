"""Freeze native NYC inventory adaptation; no live annotations or sealed test pixels."""
import argparse
import json
import os
from pathlib import Path
import shutil
import tarfile

import pandas as pd
from urban_tree_ml.prepared_finetune import sha, validate_recipe

RUN='nyc-naip-swin-30cm-v8-adaptation'
BASE='nyc-naip-native30-v7-baseline'
PARENT='sf-boston-naip-swin-30cm-v7-finetune'
REMOTE='/lambda/nfs/tree-reporting-dev/urban-tree-ml'
PARENT_SHA='b04d57130251dcbfffab4857e6db6aa98c16661785d145be92d2490f52218ee6'

def save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,indent=2)+'\n')

def portable(raw,root):
    raw=json.loads(json.dumps(raw))
    old=raw['paths']['root'].replace('\\','/').rstrip('/')
    for section,field in [('imagery','local_raster'),('reference','taxonomy_path'),('reference','normalization_path')]:
        value=raw[section][field].replace('\\','/')
        if not value.startswith(old+'/'):
            raise ValueError('Config path outside original artifact root: '+value)
        raw[section][field]=value[len(old)+1:]
    return raw

def prepare(root):
    inputs=root/'run-inputs'/RUN
    if (inputs/'FREEZE_COMPLETE').exists():
        validate_recipe(root,RUN)
        print('Existing verified freeze retained');return
    inputs.mkdir(parents=True,exist_ok=True)
    base=root/'run-inputs'/BASE
    plan=json.loads((base/'split-plan.json').read_text())
    assert not plan['test_evaluated'] and not plan['test_pixels_materialized']
    configs={}
    asset_paths=set()
    for source_name,suffix in [('config.json','usnyc'),('config-central-park.json','central-park')]:
        cfg=portable(json.loads((base/source_name).read_text()),root)
        original=root/'chips'/cfg['dataset'];cfg['dataset']=RUN+'-'+suffix
        destination=root/'chips'/cfg['dataset'];destination.mkdir(parents=True,exist_ok=True)
        manifest=pd.read_parquet(original/'chips.parquet')
        assert 'test' not in set(manifest.split)
        manifest['path']=manifest.path.str.replace('\\','/',regex=False)
        manifest.to_parquet(destination/'chips.parquet',index=False)
        # Include collision/date/curation exclusion audits, not just retained labels.
        for p in original.iterdir():
            if p.is_file() and p.suffix in ('.json','.parquet') and p.name not in ('chips.parquet','summary.json'):
                shutil.copyfile(p,destination/p.name)
        summary=json.loads((original/'summary.json').read_text())
        for key,value in list(summary.items()):
            if isinstance(value,str) and value.replace('\\','/').startswith(original.as_posix()+'/'):
                summary[key]=REMOTE+'/'+destination.relative_to(root).as_posix()+'/'+Path(value).name
        summary['source_raster']=REMOTE+'/'+cfg['imagery']['local_raster']
        summary['prepared_from']=BASE
        save(destination/'summary.json',summary)
        for row in manifest.itertuples():
            source=original/row.path;target=destination/row.path
            target.parent.mkdir(parents=True,exist_ok=True)
            if not target.exists():
                os.link(source,target)  # Immutable frozen payload; no extra local pixel copy.
            assert sha(source)==sha(target)
        asset_paths.update(p for p in destination.rglob('*') if p.is_file())
        configs[suffix]=cfg
    training=configs['usnyc']
    training['training'].update(epochs=80,learning_rate=3e-5,batch_size=2,workers=4,accumulate_grad_batches=16)
    evaluations=[dict(config=training,split='validation',cohort='validation-usnyc'),
        dict(config=training,split='train',cohort='train-usnyc'),
        dict(config=configs['central-park'],split='train',cohort='train-usnyc-central-park',prediction_only=True)]
    for city,cohort in [('ussfo','validation'),('usbos','validation-usbos')]:
        cfg=portable(json.loads((root/'runs'/PARENT/f'config-{city}.json').read_text()),root)
        # Identical vocabulary/normalization, pinned to the same explicit parent files.
        cfg['reference']=training['reference'].copy()
        evaluations.append(dict(config=cfg,split='validation',cohort=cohort))
    raster=root/training['imagery']['local_raster']
    assert sha(raster)==plan['source_sha256']
    asset_paths.add(raster)
    asset_paths.add(raster.with_suffix('.manifest.json'))
    for field in ['taxonomy_path','normalization_path']:
        asset_paths.add(root/training['reference'][field])
    shutil.copyfile(base/'split-plan.json',inputs/'split-plan.json')
    shutil.copyfile(base/'development-inventory.parquet',inputs/'inventory.parquet')
    save(inputs/'resolution-policy.json',{'recipe':'swin-native30-upsample60-v1'})
    checkpoint=f'runs/{PARENT}/checkpoints/007.ckpt'
    assert sha(root/checkpoint)==PARENT_SHA
    checksums={p.relative_to(root).as_posix():sha(p) for p in asset_paths if p.suffix!='.npz'}
    checksums[(inputs/'split-plan.json').relative_to(root).as_posix()]=sha(inputs/'split-plan.json')
    checksums[(inputs/'inventory.parquet').relative_to(root).as_posix()]=sha(inputs/'inventory.parquet')
    pixels=[dict(path=p.relative_to(root).as_posix(),sha256=sha(p)) for p in sorted(asset_paths) if p.suffix=='.npz']
    recipe=dict(experiment=RUN,parent=PARENT,checkpoint=checkpoint,checkpoint_sha256=PARENT_SHA,
        test_evaluated=False,training_config=training,evaluations=evaluations,checksums=checksums,pixel_checksums=pixels,
        training_scope='NYC inventory only; frozen SF/Boston validation retention; Central Park prediction only')
    save(inputs/'prepared-finetune.json',recipe)
    validate_recipe(root,RUN)
    receipt={p.relative_to(root).as_posix():sha(p) for p in sorted(asset_paths)}
    save(root/(RUN+'-assets.json'),receipt)
    with tarfile.open(root/(RUN+'-assets.tar'),'w',dereference=True) as archive:
        for p in sorted(asset_paths):archive.add(p,arcname=p.relative_to(root).as_posix(),recursive=False)
    (inputs/'FREEZE_COMPLETE').write_text('Frozen native NYC inventory; no test pixels or live curation.\n')
    with tarfile.open(root/(RUN+'-inputs.tar.gz'),'w:gz') as archive:
        archive.add(inputs,arcname=RUN)
    print(json.dumps(dict(experiment=RUN,assets=len(receipt),asset_bytes=(root/(RUN+'-assets.tar')).stat().st_size,
        train=245,validation=19,sealed_test=93,central_park_unscored=187)),flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--root',type=Path,required=True)
    prepare(parser.parse_args().root)
