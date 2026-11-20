"""Freeze a warm-start with latest SF/Boston and completed-only NYC training."""
import argparse
import json
from pathlib import Path
import shutil
import tarfile
import tempfile

import pandas as pd

import launch_curated as launch
from urban_tree_ml.curated_nyc_joint import eligible_training_ids, validate
from urban_tree_ml.freeze_next_run import freeze, validate_frozen_city
from urban_tree_ml.prepared_finetune import sha, config_from_recipe

RUN = 'sf-boston-nyc-naip-swin-30cm-v10-curated-finetune'
PARENT = 'sf-boston-naip-swin-30cm-v9-fixed-pair-finetune'
BASE = 'nyc-naip-native30-v7-baseline'


def save(path, value):
    path.write_text(json.dumps(value, indent=2)+'\n', encoding='utf-8')


def main(run=RUN, parent=PARENT, expected_parent_sha='392fbe1f422defe785dd80739bb74234a7a4e49b41c51e5640fbfc88b9b79539', vegetation_groups=False, refresh_nyc_validation=False, patience_checks=4, admit_curated_tiles=False, acquisitions=(), resume_base_freeze=False):
    if isinstance(patience_checks, bool) or not isinstance(patience_checks, int) or patience_checks < 1:
        raise ValueError('Patience checks must be a positive integer')
    # Local aliases keep all historical defaults reproducible; each new freeze is unique.
    RUN, PARENT = run, parent
    root = launch.ROOT
    launch.RUN, launch.WARM_START = RUN, PARENT
    launch.WARM_EPOCHS, launch.CROWN_HEAD, launch.RESOLUTION_30 = 80, True, True
    inputs = root/'run-inputs'/RUN
    if resume_base_freeze:
        if (inputs/'usffx').exists() or (inputs/'validation-policy.json').exists() or (root/(RUN+'-instance.json')).exists():
            raise ValueError('Base-freeze continuation is only valid before completed city preparation and allocation')
        for city, name in [('ussfo','ussfo-2022-mosaic'),('usbos','usbos-2023-external')]:
            validate_frozen_city(inputs/city)
            for file in ('manifest.json','reviews.json','training-feedback.json'):
                if (inputs/city/file).read_bytes() != (root/'qa/registration'/name/file).read_bytes():
                    raise ValueError('Base freeze changed before continuation: '+city)
        existing = json.loads((inputs/'warm-start.json').read_text())
        if existing['checkpoint_sha256'] != expected_parent_sha:
            raise ValueError('Base freeze uses another parent checkpoint')
    else:
        launch.prepare(vegetation_groups=vegetation_groups)  # Protected SF source refresh and published freeze.
    from urban_tree_ml.warm_recipe import LOSS_WEIGHTS, loss_policy_weights
    loss_policy = json.loads((launch.WORK/'configs/fine_tune_loss_policy.json').read_text())
    warm = json.loads((inputs/'warm-start.json').read_text())
    warm.update(loss_policy_weights(loss_policy))
    save(inputs/'loss-policy.json', loss_policy)
    base = root/'run-inputs'/BASE
    raw = json.loads((base/'config.json').read_text())
    nyc_review = root/'qa/registration'/Path(raw['imagery']['local_raster']).stem
    if resume_base_freeze and (inputs/'usnyc').exists():
        validate_frozen_city(inputs/'usnyc')
        for name in ('manifest.json','reviews.json','training-feedback.json'):
            if (inputs/'usnyc'/name).read_bytes() != (nyc_review/name).read_bytes():
                raise ValueError('NYC freeze changed before continuation')
    else:
        with tempfile.TemporaryDirectory(prefix='tree-nyc-freeze-') as temporary:
            staging = Path(temporary)
            prepared = staging/'prepared'; (prepared/'usnyc').mkdir(parents=True)
            shutil.copy2(base/'development-inventory.parquet', prepared/'usnyc/inventory.parquet')
            shutil.copy2(base/'taxonomy.json', prepared/'usnyc/taxonomy.json')
            save(prepared/'preparation.json', {'source': BASE, 'test_pixels_materialized': False})
            freeze(prepared, {'usnyc': nyc_review}, staging/'frozen')
            shutil.copytree(staging/'frozen/usnyc', inputs/'usnyc')
    shutil.copy2(base/'split-plan.json', inputs/'nyc-split-plan.json')
    state = validate_frozen_city(inputs/'usnyc')
    manifest = json.loads((inputs/'usnyc/manifest.json').read_text())
    plan = json.loads((inputs/'nyc-split-plan.json').read_text())
    if admit_curated_tiles:
        import rasterio
        from urban_tree_ml.curation_admission import enable, load, planned_training_ids
        for city in ('ussfo','usbos','usnyc'):
            if not resume_base_freeze or load(inputs/city) is None:
                enable(inputs/city)
        with rasterio.open(raw['imagery']['local_raster']) as source:
            ids = planned_training_ids(inputs/'usnyc', source, 512,
                {w['chip_id']: w['split'] for w in plan['windows']})
        if not ids:
            raise ValueError('NYC has no human-curated training tiles')
    else:
        ids = eligible_training_ids(manifest, state, plan)
    for section, field in [('imagery','local_raster'),('reference','taxonomy_path'),('reference','normalization_path')]:
        raw[section][field] = Path(raw[section][field]).relative_to(root).as_posix()
    if vegetation_groups:
        from urban_tree_ml.vegetation_groups import expanded_taxonomy
        taxonomy = json.loads((inputs/'ussfo/taxonomy.json').read_text())
        expanded_path = inputs/'vegetation-taxonomy.json'
        save(expanded_path, expanded_taxonomy(taxonomy))
        raw['reference']['taxonomy_path'] = expanded_path.relative_to(root).as_posix()
    raw['training'].update(augmentation='fixed_pair_v1', random_dihedral=False,
        budget_policy='source_passes_v1', early_stopping_patience_checks=patience_checks,
        epochs=80, batch_size=2, workers=8, accumulate_grad_batches=16, learning_rate=3e-5)
    raw['imagery']['boundary_shifts'] = False
    raw['training'].update({name: warm[name] for name in LOSS_WEIGHTS if name in warm})
    validation_dataset = 'nyc-naip-swin-30cm-v8-adaptation-usnyc'
    original = root/'chips'/validation_dataset
    val = pd.read_parquet(original/'chips.parquet').query("split == 'validation'")
    files = [inputs/'nyc-split-plan.json', original/'chips.parquet', original/'labels.parquet',
             original/'normalization.json', root/raw['imagery']['local_raster'],
             root/raw['reference']['taxonomy_path'], root/raw['reference']['normalization_path']]
    policy = {'experiment': RUN, 'test_evaluated': False, 'config': raw,
        'train_chip_ids': ids, 'validation_dataset': validation_dataset,
        'validation_pixels': {row.chip_id: sha(original/row.path) for row in val.itertuples()},
        'checksums': {p.relative_to(root).as_posix(): sha(p) for p in files}}
    if policy['checksums'][raw['imagery']['local_raster']] != plan['source_sha256']:
        raise ValueError('NYC imagery differs from guarded plan')
    if refresh_nyc_validation:
        policy['validation_supervision']='frozen-curation-v1'
    save(inputs/'curated-nyc-joint.json', policy)
    cities = ['ussfo','usbos','usnyc']
    fairfax_raw = None
    if admit_curated_tiles:
        from prepare_fairfax_joint import prepare as prepare_fairfax
        fairfax_raw, fairfax_receipt = prepare_fairfax(root, inputs, inputs/'ussfo/taxonomy.json')
        cities.append('usffx')
    dates = json.loads((inputs/'imagery-dates.json').read_text())
    dates['usnyc'] = {'cutoff': raw['imagery']['planting_date_cutoff']}
    if fairfax_raw is not None:
        dates['usffx'] = {'cutoff': fairfax_raw['imagery']['planting_date_cutoff']}
    save(inputs/'imagery-dates.json', dates)
    if warm['checkpoint_sha256'] != expected_parent_sha:
        raise ValueError('Unexpected parent checkpoint')
    warm.update(augmentation='fixed_pair_v1', random_dihedral=False,
        budget_policy='source_passes_v1', early_stopping_patience_checks=patience_checks)
    if vegetation_groups:
        warm['vegetation_groups'] = True
    save(inputs/'warm-start.json', warm)
    from urban_tree_ml.curated_validation import freeze_policy, POLICY
    freeze_policy(inputs, cities)
    warm['validation_policy'] = POLICY
    save(inputs/'warm-start.json', warm)
    validate(root, RUN)
    # Real local NYC target build before allocating GPU time. No live mutation.
    from urban_tree_ml.chips import build_chips
    cfg = config_from_recipe(root, raw, RUN)
    built = build_chips(cfg, cfg.imagery.local_raster,
        feedback_path=inputs/'usnyc/training-feedback.json',
        inventory_source=inputs/'usnyc/inventory.parquet',
        output_dataset=RUN+'-nyc-preflight', allowed_chip_ids=set(ids), allowed_splits={'train'})
    actual = pd.read_parquet(built['manifest'])
    if set(actual.chip_id) != set(ids) or set(actual.split) != {'train'}:
        raise ValueError('NYC curated target materialization changed admitted chip coverage')
    save(inputs/'nyc-build-preflight.json', {'chips': len(actual), 'targets': built['trees'],
        'chip_ids': ids, 'early_stopping_patience_checks': patience_checks})
    if fairfax_raw is not None:
        from urban_tree_ml.warm_recipe import apply_warm_recipe
        fcfg = config_from_recipe(root,fairfax_raw,RUN)
        apply_warm_recipe(fcfg,warm)
        fcfg.reference.taxonomy_path=inputs/'vegetation-taxonomy.json' if vegetation_groups else inputs/'ussfo/taxonomy.json'
        fbuilt = build_chips(fcfg,fcfg.imagery.local_raster,
            feedback_path=inputs/'usffx/training-feedback.json',inventory_source=inputs/'usffx/inventory.parquet',
            output_dataset=RUN+'-fairfax-preflight')
        save(inputs/'fairfax-build-preflight.json',fbuilt)
    if refresh_nyc_validation:
        from urban_tree_ml.curated_nyc_joint import build_curated_validation
        from urban_tree_ml.warm_recipe import apply_warm_recipe
        from urban_tree_ml.config import TargetsConfig
        apply_warm_recipe(cfg,warm)
        cfg.targets=TargetsConfig.model_validate({**cfg.targets.model_dump(),
            **json.loads((inputs/'center-policy.json').read_text())})
        current=build_curated_validation(root,RUN,cfg,policy)
        save(inputs/'nyc-validation-preflight.json',dict(
            chips=len(pd.read_parquet(current/'chips.parquet')),
            targets=len(pd.read_parquet(current/'labels.parquet')),
            pixels_identical=True,supervision='frozen-curation-v1'))
    if acquisitions:
        if not admit_curated_tiles:
            raise ValueError('Temporal acquisitions require published curation admission')
        from prepare_temporal_training import prepare as prepare_temporal
        prepare_temporal(root, inputs, PARENT, acquisitions)
        from urban_tree_ml.temporal_training import build_sources
        from urban_tree_ml.config import ReferenceConfig
        preflight_run = root/'qa'/RUN/'temporal-preflight'
        preflight_run.mkdir(parents=True, exist_ok=False)
        reference = ReferenceConfig(taxonomy_path=inputs/'vegetation-taxonomy.json',
            normalization_path=root/'chips'/(PARENT+'-ussfo')/'normalization.json')
        build_sources(root, RUN, reference, warm, preflight_run)
    # Refuse allocation from a snapshot that was already stale when preparation ended.
    for city, live in [('ussfo', root/'qa/registration/ussfo-2022-mosaic'),
                       ('usbos', root/'qa/registration/usbos-2023-external'), ('usnyc',nyc_review),
                       *([('usffx',root/'qa/registration/fairfax-naip-2023-curation-v1')] if fairfax_raw is not None else [])]:
        for name in ('manifest.json','reviews.json','training-feedback.json'):
            if (inputs/city/name).read_bytes() != (live/name).read_bytes():
                raise ValueError('Curation changed during preparation: '+city)
    with tarfile.open(root/(RUN+'-inputs.tar.gz'), 'w:gz') as archive:
        archive.add(inputs, arcname=RUN)
    print(json.dumps({'run': RUN, 'nyc_curated_train': len(ids), 'nyc_validation': len(val),
        'nyc_retained_targets': built['trees'], 'patience_checks': patience_checks, 'test_sealed': True}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--experiment', default=RUN)
    parser.add_argument('--parent', default=PARENT)
    parser.add_argument('--parent-sha', default='392fbe1f422defe785dd80739bb74234a7a4e49b41c51e5640fbfc88b9b79539')
    parser.add_argument('--vegetation-groups', action='store_true')
    parser.add_argument('--refresh-nyc-validation', action='store_true')
    parser.add_argument('--patience-checks', type=int, default=4)
    parser.add_argument('--admit-curated-tiles', action='store_true')
    parser.add_argument('--acquisitions', nargs='*', default=[])
    parser.add_argument('--resume-base-freeze', action='store_true')
    args = parser.parse_args()
    for name in (args.experiment, args.parent):
        if not name or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-' for c in name):
            parser.error('Run names must contain lowercase letters, digits, and hyphens only')
    if len(args.parent_sha) != 64 or any(c not in '0123456789abcdef' for c in args.parent_sha):
        parser.error('Expected checkpoint SHA256 must be 64 lowercase hexadecimal characters')
    main(args.experiment, args.parent, args.parent_sha, args.vegetation_groups, args.refresh_nyc_validation, args.patience_checks, args.admit_curated_tiles, args.acquisitions, args.resume_base_freeze)
