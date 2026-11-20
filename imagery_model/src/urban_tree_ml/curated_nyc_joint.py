"""Fail-closed admission of completed NYC training chips into a joint run."""
import json
from pathlib import Path

import pandas as pd
import numpy as np

from urban_tree_ml.curated_only import completed_chips
from urban_tree_ml.freeze_next_run import validate_frozen_city
from urban_tree_ml.prepared_finetune import config_from_recipe, resolve, sha


def eligible_training_ids(manifest, state, split_plan):
    completed = set(completed_chips(manifest, state))
    full = {s['validation_chip_id'] for s in manifest['scenes']
            if s.get('validation_chip_id') in completed and
            s.get('image_width') == 512 and s.get('image_height') == 512}
    planned = {w['chip_id'] for w in split_plan['windows'] if w['split'] == 'train'}
    if not full or not full <= planned or completed != full:
        raise ValueError('NYC requires completed full 512px chips in the frozen training split')
    return sorted(full)


def validate(root, experiment):
    inputs = root/'run-inputs'/experiment
    policy = json.loads((inputs/'curated-nyc-joint.json').read_text())
    if policy['experiment'] != experiment or policy['test_evaluated'] is not False:
        raise ValueError('NYC policy identity/test mismatch')
    if policy.get('validation_supervision','legacy-pinned') not in {'legacy-pinned','frozen-curation-v1'}:
        raise ValueError('Unknown NYC validation supervision policy')
    state = validate_frozen_city(inputs/'usnyc')
    manifest = json.loads((inputs/'usnyc/manifest.json').read_text())
    plan = json.loads((inputs/'nyc-split-plan.json').read_text())
    if plan['test_evaluated'] or plan['test_pixels_materialized']:
        raise ValueError('NYC test must remain sealed')
    from urban_tree_ml.curation_admission import load as load_admission, planned_training_ids
    if load_admission(inputs/'usnyc') is not None:
        import rasterio
        preliminary = config_from_recipe(root, policy['config'], experiment)
        with rasterio.open(preliminary.imagery.local_raster) as source:
            ids = planned_training_ids(inputs/'usnyc', source, 512,
                {w['chip_id']: w['split'] for w in plan['windows']})
    else:
        ids = eligible_training_ids(manifest, state, plan)
    if ids != policy['train_chip_ids']:
        raise ValueError('NYC training allowlist differs from frozen curation policy')
    for relative, expected in policy['checksums'].items():
        if sha(resolve(root, relative)) != expected:
            raise ValueError('NYC frozen checksum mismatch: '+relative)
    config = config_from_recipe(root, policy['config'], experiment)
    if config.inventory.city.lower() != 'usnyc' or config.imagery.boundary_shifts:
        raise ValueError('NYC must use its native grid without unreviewed shifted windows')
    if config.imagery.chip_pixels != 512 or config.imagery.resolution_m != .3:
        raise ValueError('NYC curated eligibility is pinned to the native 512px/30cm grid')
    original = root/'chips'/policy['validation_dataset']
    frame = pd.read_parquet(original/'chips.parquet')
    val = frame[frame.split == 'validation']
    planned_validation = {w['chip_id'] for w in plan['windows'] if w['split'] == 'validation'}
    if set(val.chip_id) != planned_validation or set(ids) & planned_validation:
        raise ValueError('NYC validation differs from guarded split plan')
    for row in val.itertuples():
        if sha(original/row.path) != policy['validation_pixels'][row.chip_id]:
            raise ValueError('NYC frozen validation pixel mismatch')
    return policy, config


def build_curated_validation(root, experiment, config, policy):
    """Fixed validation pixels/windows, current frozen labels and ALL loss targets."""
    from urban_tree_ml.chips import build_chips
    original=root/'chips'/policy['validation_dataset']
    pinned=pd.read_parquet(original/'chips.parquet').query("split == 'validation'")
    cfg=config.model_copy(deep=True);cfg.imagery.boundary_shifts=False
    dataset=experiment+'-usnyc-validation-current'
    destination=root/'chips'/dataset
    if destination.exists():raise ValueError('Current NYC validation destination already exists')
    result=build_chips(cfg,cfg.imagery.local_raster,
        feedback_path=root/'run-inputs'/experiment/'usnyc/training-feedback.json',
        inventory_source=root/'run-inputs'/experiment/'usnyc/inventory.parquet',
        output_dataset=dataset,allowed_chip_ids=set(pinned.chip_id),allowed_splits={'validation'},
        validation_supervision_only=True,validation_full_targets=True)
    fresh=pd.read_parquet(result['manifest']).set_index('chip_id')
    for row in pinned.itertuples():
        if sha(original/row.path)!=policy['validation_pixels'][row.chip_id]:
            raise ValueError('Pinned NYC validation asset changed')
        with np.load(original/row.path) as old, np.load(destination/fresh.loc[row.chip_id,'path']) as new:
            if not np.array_equal(old['image'],new['image']):
                raise ValueError('Rebuilt NYC validation pixels differ from pinned imagery')
            if not {'center','species','genus','detection_mask','scoring_mask','crown','crown_mask','single_tree_groups'}<=set(new.files):
                raise ValueError('NYC validation training targets incomplete')
    return destination


def pin_validation_and_audit(root, experiment, destination, policy, *, config=None):
    """Keep only admitted training pixels and add immutable NYC validation."""
    original = root/'chips'/policy['validation_dataset']
    validation_source=original
    if policy.get('validation_supervision')=='frozen-curation-v1':
        if config is None:raise ValueError('Current validation requires actual training config')
        validation_source=build_curated_validation(root,experiment,config,policy)
    expected = set(policy['train_chip_ids'])
    for name in ('chips.parquet', 'labels.parquet'):
        fresh = pd.read_parquet(destination/name)
        if not set(fresh.split) <= {'train'} or not set(fresh.chip_id) <= expected:
            raise ValueError('Uncurated or non-training NYC input materialized')
        if name == 'chips.parquet' and set(fresh.chip_id) != expected:
            raise ValueError('Some completed NYC chips failed materialization; do not silently omit')
        old = pd.read_parquet(validation_source/name)
        validation = old[old.split == 'validation'].copy()
        if name == 'chips.parquet':
            validation['path'] = validation.path.map(lambda p: f'../{validation_source.name}/'+str(p).replace('\\','/'))
        pd.concat([fresh, validation], ignore_index=True).to_parquet(destination/name, index=False)
    frame = pd.read_parquet(destination/'chips.parquet')
    audit = {'train_chip_ids': sorted(expected), 'train_chips': len(expected),
             'validation_chips': int(frame.split.eq('validation').sum()),
             'mode': ('any_human_curation_v1' if (root/'run-inputs'/experiment/'usnyc/tile-admission.json').exists()
                      else 'completed_full_chips_only'), 'unreviewed_training_chips': 0,
             'validation_dataset': policy['validation_dataset'], 'test_evaluated': False,
             'validation_supervision':policy.get('validation_supervision','legacy-pinned'),
             'validation_target_dataset':validation_source.name}
    (root/'runs'/experiment/'nyc-curated-admission.json').write_text(json.dumps(audit, indent=2)+'\n')
    print('NYC curated-only admission verified:', json.dumps(audit), flush=True)
