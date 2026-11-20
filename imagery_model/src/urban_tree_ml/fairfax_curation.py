"""Register Fairfax review pixels and freeze strictly curated development cohorts."""
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from urban_tree_ml.config import load_config
from urban_tree_ml.fairfax_pilot import load_bundle, sample_windows, save_new, sha256

RUN = 'fairfax-naip-2023-curation-v1'


def verify_pilot(pilot):
    pilot = Path(pilot).resolve()
    receipt = json.loads((pilot / 'preparation-receipt.json').read_text())
    if not (pilot / 'COMPLETE').exists():
        raise ValueError('Fairfax preparation incomplete')
    for item in receipt['inference_inputs'] + receipt['source_files']:
        if sha256(item['path']) != item['sha256']:
            raise ValueError('Frozen Fairfax input changed: ' + item['path'])
    if sha256(receipt['source_vrt']) != receipt['source_vrt_sha256']:
        raise ValueError('Fairfax source mosaic changed')
    return load_config(pilot / 'config.json'), receipt


def register(pilot, root, annotations):
    """Import immutable pilot copies and create an empty, full-tile review workspace."""
    import rasterio
    from urban_tree_ml.quality import append_validation_chip_to_registration_review

    pilot, root = Path(pilot).resolve(), Path(root).resolve()
    config, source_receipt = verify_pilot(pilot)
    source_run = pilot / 'runs' / config.experiment
    if not (source_run / 'COMPLETE').exists():
        raise ValueError('Fairfax pilot inference incomplete')
    source_dataset = pilot / 'chips' / config.dataset
    inputs, run = root / 'run-inputs' / RUN, root / 'runs' / RUN
    dataset = root / 'chips' / (RUN + '-usffx')
    review = root / 'qa' / 'registration' / RUN
    inventory = root / 'inventory/usffx/inventory.parquet'
    for path in (inputs, run, dataset, review, inventory):
        if path.exists():
            raise ValueError(f'Fairfax registration destination exists: {path}')
    inputs.mkdir(parents=True)
    config.experiment, config.dataset = RUN, dataset.name
    config.paths.root, config.paths.annotations = root, Path(annotations).resolve()
    config.inventory.parquet_url = str(inventory)
    inventory.parent.mkdir(parents=True, exist_ok=True)
    # A schema-only inventory lets the standard editor add human-confirmed trees.
    empty = pd.read_parquet(root / 'inventory/usbos/inventory.parquet').iloc[:0].copy()
    empty.to_parquet(inventory, index=False)
    save_new(inputs / 'config.json', config.model_dump(mode='json'))
    shutil.copy2(pilot / 'sample-plan.json', inputs / 'sample-plan.json')
    shutil.copytree(source_dataset, dataset)
    summary = json.loads((dataset / 'summary.json').read_text())
    summary['manifest'] = str(dataset / 'chips.parquet')
    (dataset / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    shutil.copytree(source_run / 'evaluation', run / 'evaluation')
    for directory in (run / 'evaluation').iterdir():
        metadata_path = directory / 'evaluation-metadata.json'
        metadata = json.loads(metadata_path.read_text())
        metadata.update(config=config.model_dump(mode='json'), run_id=RUN)
        metadata['imported_from'] = str(source_run)
        metadata_path.write_text(json.dumps(metadata, indent=2) + '\n')
        metrics_path = directory / 'metrics.json'
        metrics = json.loads(metrics_path.read_text())
        metrics.update(run_id=RUN, dataset=config.dataset)
        metrics_path.write_text(json.dumps(metrics, indent=2) + '\n')
    review.mkdir(parents=True)
    metadata = dict(review_id=RUN+'-development', curation_schema_version=2,
                    source_raster=str(config.imagery.local_raster), inventory=str(inventory),
                    city='usffx', window_pixels=512, included_splits=['train', 'validation'],
                    test_labels_included=False, generated_at=datetime.now(UTC).isoformat())
    with rasterio.open(config.imagery.local_raster) as source:
        metadata['curation_crs'] = source.crs.to_string()
    manifest = {'metadata': metadata, 'samples': [], 'scenes': []}
    chips = pd.read_parquet(dataset / 'chips.parquet')
    if set(chips.split) != {'train', 'validation'} or chips.chip_id.duplicated().any():
        raise ValueError('Unexpected Fairfax development cohort')
    for number, chip in enumerate(chips.itertuples(), 1):
        append_validation_chip_to_registration_review(config, config.imagery.local_raster, review,
            chip.chip_id, empty, _manifest=manifest, _persist=False, _inventory=empty)
        manifest['scenes'][-1]['splits'] = [chip.split]
        if number % 20 == 0:
            print(f'Rendered Fairfax review tiles {number}/{len(chips)}', flush=True)
    save_new(review / 'manifest.json', manifest)
    return finish_registration(config, pilot, source_receipt)


def finish_registration(config, pilot, source_receipt):
    from urban_tree_ml.curation_archive import acquisition
    from urban_tree_ml.feedback import persist_review_payload, snapshot_registration_annotations
    from urban_tree_ml.quality import _render_registration_html

    root = config.paths.root
    inputs, run = root / 'run-inputs' / RUN, root / 'runs' / RUN
    review = root / 'qa/registration' / RUN
    if (review / 'reviews.json').exists():
        raise ValueError('Review state already initialized; preserve saved edits')
    manifest = json.loads((review / 'manifest.json').read_text())
    metadata = manifest['metadata']
    chips = pd.read_parquet(root / 'chips' / config.dataset / 'chips.parquet')
    if {s['validation_chip_id'] for s in manifest['scenes']} != set(chips.chip_id):
        raise ValueError('Incomplete Fairfax review import')
    persist_review_payload(review, {'reviews': {}, 'scene_reviews': {}, 'mask_regions': []})
    (review / 'index.html').write_text(_render_registration_html([], metadata, manifest['scenes']), encoding='utf-8')
    acquisition(root, config.imagery.local_raster, register=True)
    snapshot_registration_annotations(config, config.imagery.local_raster, review_dir=review)
    queue = root / 'benchmarks' / ('zz-' + RUN)
    queue.mkdir()
    target = queue / 'validation-targets.csv'
    pd.DataFrame([dict(city='usffx', chip_id=chip.chip_id,
                      queue='Fairfax ' + chip.split,
                      reason='Review the full tile and Mark more done before training admission')
                  for chip in chips.itertuples()]).to_csv(target, index=False)
    save_new(queue / 'manifest.json', {'new_run': RUN, 'output_sha256': {target.name: sha256(target)}})
    (queue / 'COMPLETE').write_text('Curator assignments ready\n')
    receipt = {'run': RUN, 'dataset': config.dataset, 'review': str(review),
               'source_pilot': str(pilot), 'source_receipt_sha256': sha256(pilot / 'preparation-receipt.json'),
               'sample_plan_sha256': sha256(inputs / 'sample-plan.json'),
               'source_files': source_receipt['source_files'],
               'raster_sha256': sha256(config.imagery.local_raster),
               'counts': chips.groupby('split').size().to_dict(), 'labels': 'unavailable',
               'admission': 'full tile more_done required for train and validation'}
    save_new(inputs / 'registration-receipt.json', receipt)
    (run / 'COMPLETE').write_text('Imported prediction-only review cohorts\n')
    return receipt


def seed_holdout(pilot, bundle, root, count=40):
    """Reserve raw pixels in an isolated sealed directory, outside model datasets."""
    import rasterio
    from rasterio.windows import Window

    config, receipt = verify_pilot(pilot)
    _, _, boundary = load_bundle(bundle)
    output = Path(root) / 'holdouts' / 'fairfax-naip-2023-v1'
    if output.exists():
        raise ValueError('Fairfax holdout already exists; preserve the sealed selection')
    with rasterio.Env(GDAL_CACHEMAX=128 * 1024 * 1024), rasterio.open(config.imagery.local_raster) as source:
        plan = sample_windows(source, boundary, config, {'test': count}, holdout_only=True)
        development = json.loads((Path(pilot) / 'sample-plan.json').read_text())
        dev_blocks = {(item['block_x'], item['block_y']) for item in development['windows']}
        if any((item['block_x'], item['block_y']) in dev_blocks for item in plan['windows']):
            raise ValueError('Holdout overlaps development blocks')
        output.mkdir(parents=True)
        pixels = output / 'pixels'
        pixels.mkdir()
        for item in plan['windows']:
            chip = f"r{item['row']//512:06d}_c{item['col']//512:06d}"
            path = pixels / (chip + '.npz')
            window = Window(item['col'], item['row'], 512, 512)
            np.savez_compressed(path, image=source.read(config.imagery.bands, window=window),
                                valid_mask=np.all(source.read_masks(config.imagery.bands, window=window) > 0, axis=0))
            item.update(chip_id=chip, path=path.relative_to(output).as_posix(), sha256=sha256(path))
    plan.update(status='sealed', label_status='unavailable', predictions_generated=False,
                source_pilot=str(Path(pilot).resolve()), source_files=receipt['source_files'],
                source_raster=str(config.imagery.local_raster), raster_sha256=sha256(config.imagery.local_raster))
    save_new(output / 'manifest.json', plan)
    (output / 'SEALED').write_text('Reserved test imagery. Evaluation requires explicit authorization.\n')
    return {'output': str(output), 'tiles': count, 'blocks': len({(w['block_x'], w['block_y']) for w in plan['windows']})}


def admitted_ids(manifest, state, plan, *, touched=None):
    """Match explicit full-tile completion to immutable development split membership."""
    planned = {f"r{w['row']//512:06d}_c{w['col']//512:06d}": w['split'] for w in plan['windows']}
    if len(planned) != len(plan['windows']) or not set(planned.values()) <= {'train', 'validation'}:
        raise ValueError('Invalid Fairfax development split plan')
    admitted = {'train': [], 'validation': []}
    for scene in manifest['scenes']:
        if state['scene_reviews'].get(scene['scene_id'], {}).get('more_done') is not True:
            continue
        chip = scene.get('validation_chip_id')
        if (chip not in planned or scene.get('image_width') != 512 or scene.get('image_height') != 512
                or (touched is None and scene.get('splits') != [planned[chip]])):
            raise ValueError('Fairfax completion must identify a full planned development tile')
        admitted[planned[chip]].append(chip)
    if touched is not None:
        admitted['train'] = [chip for chip, split in planned.items() if split == 'train' and chip in touched]
    return {split: sorted(set(ids)) for split, ids in admitted.items()}


def development_plan(registration, inventory):
    """Combine the original immutable plan with verified inventory-review additions."""
    registration = Path(registration)
    base = registration / 'sample-plan.json'
    plan = json.loads(base.read_text())
    seen = {(w['row'], w['col']) for w in plan['windows']}
    for path in sorted((registration / 'development-extensions').glob('*.json')):
        extension = json.loads(path.read_text())
        if (extension['base_plan_sha256'] != sha256(base)
                or extension['inventory_sha256'] != sha256(inventory)
                or extension['seed'] != plan['seed']
                or extension['split_policy'] != plan['split_policy']):
            raise ValueError('Fairfax development extension differs from its frozen inputs')
        for window in extension['windows']:
            key = (window['row'], window['col'])
            if key in seen or window['split'] not in {'train', 'validation'}:
                raise ValueError('Fairfax development extension overlaps or includes test windows')
            seen.add(key)
            plan['windows'].append(window)
    return plan


def freeze_training(root, destination, taxonomy, *, any_curation=False):
    """Freeze published human labels and the admitted development windows."""
    import tempfile
    from urban_tree_ml.feedback import load_persisted_reviews
    from urban_tree_ml.freeze_next_run import freeze, validate_frozen_city

    root, destination = Path(root).resolve(), Path(destination).resolve()
    registration = root / 'run-inputs' / RUN
    config = load_config(registration / 'config.json')
    review = root / 'qa/registration' / RUN
    plan_path = registration / 'sample-plan.json'
    plan = development_plan(registration, root / 'inventory/usffx/inventory.parquet')
    receipt = json.loads((registration / 'registration-receipt.json').read_text())
    if sha256(plan_path) != receipt['sample_plan_sha256']:
        raise ValueError('Fairfax development plan changed')
    manifest = json.loads((review/'manifest.json').read_text())
    state = load_persisted_reviews(review)
    touched = None
    if any_curation:
        import rasterio
        from urban_tree_ml.curation_admission import touched_tiles
        with rasterio.open(config.imagery.local_raster) as source:
            touched = touched_tiles(manifest,state,source,512,json.loads((review/'training-feedback.json').read_text()))
    ids = admitted_ids(manifest, state, plan, touched=touched)
    if not all(ids.values()):
        raise ValueError('Fairfax needs published full-tile more_done curation in both train and validation')
    for item in receipt['source_files']:
        if sha256(item['path']) != item['sha256']:
            raise ValueError('Fairfax imagery changed')
    if sha256(config.imagery.local_raster) != receipt['raster_sha256']:
        raise ValueError('Fairfax model raster changed')
    with tempfile.TemporaryDirectory(prefix='fairfax-freeze-') as temporary:
        prepared = Path(temporary) / 'prepared'
        (prepared / 'usffx').mkdir(parents=True)
        shutil.copy2(root / 'inventory/usffx/inventory.parquet', prepared / 'usffx/inventory.parquet')
        shutil.copy2(taxonomy, prepared / 'usffx/taxonomy.json')
        save_new(prepared / 'preparation.json', {'city': 'usffx', 'admission': 'full_tile_more_done'})
        freeze(prepared, {'usffx': review}, destination)
    frozen = destination / 'usffx'
    state = validate_frozen_city(frozen)
    if admitted_ids(json.loads((frozen / 'manifest.json').read_text()), state, plan, touched=touched) != ids:
        raise ValueError('Fairfax curation changed during freeze')
    save_new(frozen / 'sample-plan.json', plan)
    policy = {'schema_version': 1, 'admitted': ids, 'plan_sha256': sha256(frozen / 'sample-plan.json'),
              'integrity_sha256': sha256(frozen / 'integrity.json'),
              'raster_sha256': receipt['raster_sha256'], 'source_files': receipt['source_files'],
              'split_policy': config.split.model_dump(mode='json'), 'seed': config.seed}
    if any_curation:
        from urban_tree_ml.curation_admission import enable
        enable(frozen)
        policy.update(schema_version=2, raster=str(config.imagery.local_raster))
    save_new(frozen / 'fairfax-admission.json', policy)
    config.reference.taxonomy_path = frozen / 'taxonomy.json'
    save_new(destination / 'config.json', config.model_dump(mode='json'))
    return {'directory': str(destination), 'admitted': ids}


def _policy_path(directory, policy, path):
    if policy.get('portable_root_relative'):
        from urban_tree_ml.prepared_finetune import resolve
        directory = Path(directory).resolve()
        if directory.parent.parent.name != 'run-inputs':
            raise ValueError('Portable Fairfax policy must live under run-inputs/<run>/<city>')
        return resolve(directory.parents[2], path)
    return Path(path)


def validate_admission(directory):
    from urban_tree_ml.freeze_next_run import validate_frozen_city

    directory = Path(directory)
    policy = json.loads((directory / 'fairfax-admission.json').read_text())
    if policy.get('schema_version') not in {1,2}:
        raise ValueError('Unknown Fairfax admission policy')
    if (sha256(directory / 'sample-plan.json') != policy['plan_sha256']
            or sha256(directory / 'integrity.json') != policy['integrity_sha256']):
        raise ValueError('Fairfax admission source changed')
    state = validate_frozen_city(directory)
    plan = json.loads((directory / 'sample-plan.json').read_text())
    manifest = json.loads((directory/'manifest.json').read_text())
    touched = None
    if policy['schema_version'] == 2:
        import rasterio
        from urban_tree_ml.curation_admission import load, touched_tiles
        context = load(directory)
        if context is None:
            raise ValueError('Missing Fairfax any-curation admission policy')
        with rasterio.open(_policy_path(directory, policy, policy['raster'])) as source:
            touched = touched_tiles(manifest,state,source,512,context[2])
    ids = admitted_ids(manifest, state, plan, touched=touched)
    if ids != policy['admitted'] or not all(ids.values()):
        raise ValueError('Fairfax admitted cohort changed or is empty')
    return policy


def build_admission(config, raster, feedback, inventory):
    if feedback is None or inventory is None:
        raise ValueError('Fairfax requires explicit frozen curated inputs')
    directory = Path(feedback).parent
    policy = validate_admission(directory)
    if (Path(feedback).resolve() != (directory / 'training-feedback.json').resolve()
            or Path(inventory).resolve() != (directory / 'inventory.parquet').resolve()
            or config.imagery.chip_pixels != 512 or config.imagery.boundary_shifts
            or config.split.model_dump(mode='json') != policy['split_policy']
            or config.seed != policy['seed'] or sha256(raster) != policy['raster_sha256']):
        raise ValueError('Fairfax build differs from frozen curated grid/inputs')
    for item in policy['source_files']:
        if sha256(_policy_path(directory, policy, item['path'])) != item['sha256']:
            raise ValueError('Fairfax source imagery changed')
    return {chip: split for split, ids in policy['admitted'].items() for chip in ids}


def record_dataset(directory, frozen, records):
    policy = validate_admission(frozen)
    expected = {(chip, split) for split, ids in policy['admitted'].items() for chip in ids}
    if {(r['chip_id'], r['split']) for r in records} != expected:
        raise ValueError('Fairfax materialization differs from curated admission')
    files = ['chips.parquet', 'labels.parquet'] + [r['path'] for r in records]
    save_new(directory / 'fairfax-curation.json', {
        'frozen': str(Path(frozen).resolve()),
        'policy_sha256': sha256(Path(frozen) / 'fairfax-admission.json'),
        'sha256': {str(name): sha256(directory / name) for name in files}})


def verify_dataset(directory):
    directory = Path(directory)
    receipt = json.loads((directory / 'fairfax-curation.json').read_text())
    frozen = Path(receipt['frozen'])
    if sha256(frozen / 'fairfax-admission.json') != receipt['policy_sha256']:
        raise ValueError('Fairfax dataset policy changed')
    policy = validate_admission(frozen)
    for name, expected in receipt['sha256'].items():
        if sha256(directory / name) != expected:
            raise ValueError('Fairfax curated dataset changed: ' + name)
    frame = pd.read_parquet(directory / 'chips.parquet')
    expected = {(chip, split) for split, ids in policy['admitted'].items() for chip in ids}
    if frame.chip_id.duplicated().any() or set(zip(frame.chip_id, frame.split)) != expected:
        raise ValueError('Fairfax dataset admission mismatch')
    return frame


def verify_training_admission(config, manifest_path):
    """Guard standalone and combined training through the public training entry."""
    frame = pd.read_parquet(manifest_path)
    if 'city' in frame and frame.city.str.lower().eq('usffx').any():
        summary = json.loads(manifest_path.with_name('summary.json').read_text())
        source = Path(summary['fairfax_source'])
        approved = verify_dataset(source).copy()
        approved['path'] = approved.path.map(lambda p: (source / p).resolve())
        selected = frame[frame.city.str.lower().eq('usffx')].copy()
        selected['path'] = selected.path.map(lambda p: (manifest_path.parent / p).resolve())
        expected = set(zip(approved.chip_id, approved.split, approved.path))
        actual = set(zip(selected.source_chip_id, selected.split, selected.path))
        if actual != expected or len(selected) != len(approved):
            raise ValueError('Joint Fairfax rows differ from curated source')
    elif config.inventory.city.lower() == 'usffx':
        verify_dataset(manifest_path.parent)
