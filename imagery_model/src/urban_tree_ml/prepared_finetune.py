"""Weights-only adaptation of frozen, prebuilt chips under the shared supervisor."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath

import pandas as pd
from urban_tree_ml.config import ProjectConfig


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def resolve(root, relative):
    value=PurePosixPath(relative)
    if value.is_absolute() or '..' in value.parts or '\\' in relative or ':' in relative:
        raise ValueError('Recipe artifact paths must be relative to the artifact root')
    return root.joinpath(*value.parts)


def config_from_recipe(root, raw, experiment):
    raw=json.loads(json.dumps(raw))
    raw['experiment']=experiment
    raw['paths']={'root':str(root),'annotations':str(root/'unused-frozen-annotations')}
    raw['imagery']['local_raster']=str(resolve(root,raw['imagery']['local_raster']))
    for field in ('taxonomy_path','normalization_path'):
        raw['reference'][field]=str(resolve(root,raw['reference'][field]))
    return ProjectConfig.model_validate(raw)


def validate_recipe(root, experiment, *, pixels=True, remote=False):
    inputs=root/'run-inputs'/experiment
    recipe=json.loads((inputs/'prepared-finetune.json').read_text())
    if recipe['experiment']!=experiment or recipe.get('test_evaluated') is not False:
        raise ValueError('Recipe identity/sealed-test policy mismatch')
    config=config_from_recipe(root,recipe['training_config'],experiment)
    for path,expected in recipe['checksums'].items():
        if sha(resolve(root,path))!=expected:
            raise ValueError(f'Frozen artifact checksum mismatch: {path}')
    if sha(resolve(root,recipe['checkpoint']))!=recipe['checkpoint_sha256']:
        raise ValueError('Warm-start checkpoint hash mismatch')
    training_root=root/'chips'/config.dataset
    manifest=pd.read_parquet(training_root/'chips.parquet')
    if not {'train','validation'}<=set(manifest.split) or 'test' in set(manifest.split):
        raise ValueError('Adaptation input must contain development only, never sealed test chips')
    if json.loads((training_root/'summary.json').read_text()).get('label_status')=='unavailable':
        raise ValueError('Cannot train on unlabeled prediction-only imagery')
    if manifest.chip_id.duplicated().any():
        raise ValueError('Duplicate training chip IDs')
    plan=json.loads((inputs/'split-plan.json').read_text())
    expected={(w['chip_id'],w['split']) for w in plan['windows'] if w['split']!='test'}
    actual=set(zip(manifest.chip_id,manifest.split))
    if actual!=expected:
        raise ValueError('Development chips differ from frozen guarded split plan')
    labels=pd.read_parquet(training_root/'labels.parquet')
    if not set(zip(labels.chip_id,labels.split))<=actual:
        raise ValueError('Labels outside development split plan')
    expected_pixels={(training_root/p).relative_to(root).as_posix() for p in manifest.path}
    if not expected_pixels <= {p['path'] for p in recipe['pixel_checksums']}:
        raise ValueError('Training pixel receipt is incomplete')
    if config.imagery.chip_pixels!=512 or config.imagery.resolution_m!=.3:
        raise ValueError('Prepared adaptation requires the verified 512px native30 grid')
    if pixels:
        for item in recipe['pixel_checksums']:
            if sha(resolve(root,item['path']))!=item['sha256']:
                raise ValueError('Chip payload checksum mismatch: '+item['path'])
    train_norm=json.loads((training_root/'normalization.json').read_text())
    reference=json.loads(config.reference.normalization_path.read_text())
    if any(train_norm[k]!=reference[k] for k in ('mean','std')):
        raise ValueError('Parent normalization changed')
    taxonomy=json.loads(config.reference.taxonomy_path.read_text())
    evaluations=[]
    for job in recipe['evaluations']:
        if job['split'] not in ('train','validation'):
            raise ValueError('Test evaluation is sealed')
        cfg=config_from_recipe(root,job['config'],experiment)
        if json.loads(cfg.reference.taxonomy_path.read_text())!=taxonomy:
            raise ValueError('Evaluation vocabulary differs from parent')
        if remote:
            m=pd.read_parquet(root/'chips'/cfg.dataset/'chips.parquet')
            if not all((root/'chips'/cfg.dataset/p).is_file() for p in m.loc[m.split==job['split'],'path']):
                raise ValueError('Evaluation chip coverage incomplete: '+cfg.dataset)
            if not cfg.imagery.local_raster.is_file():
                raise ValueError('Evaluation raster missing')
        evaluations.append((cfg,job))
    return recipe,config,evaluations


def run(root, experiment):
    from urban_tree_ml.training import run_training
    from urban_tree_ml.evaluation import run_evaluation
    recipe,config,evaluations=validate_recipe(root,experiment,remote=True)
    directory=root/'runs'/experiment
    if directory.exists():
        raise ValueError('Run already exists; refusing duplicate training')
    directory.mkdir(parents=True)
    for cfg,job in evaluations:
        # Distinct prediction-only dataset does not replace the labeled city config.
        if not job.get('prediction_only'):
            (directory/f'config-{cfg.inventory.city.lower()}.json').write_text(cfg.model_dump_json(indent=2))
    print('Frozen prepared adaptation validated; fresh optimizer; test sealed',flush=True)
    result=run_training(config,initial_checkpoint=str(resolve(root,recipe['checkpoint'])))
    (directory/'training-result.json').write_text(json.dumps(result,indent=2))
    for cfg,job in evaluations:
        print('Evaluating '+job['cohort'],flush=True)
        run_evaluation(cfg,result['best_checkpoint'],split=job['split'],cohort=job['cohort'],device_name='cuda')
    (directory/'COMPLETE').write_text('Prepared NYC adaptation and all development/retention evaluations completed; test sealed.\n')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--experiment',required=True)
    parser.add_argument('configs',nargs='*')  # Shared GPU bootstrap supplies legacy city args.
    args=parser.parse_args()
    run(Path(os.environ['TREE_ML_DATA_ROOT']),args.experiment)
