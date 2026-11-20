"""Frozen human-curated acquisitions with the original geographic split membership."""
import json
import re
from pathlib import Path

import pandas as pd

from urban_tree_ml.curation_admission import frozen_context, load, touched_tiles
from urban_tree_ml.prepared_finetune import resolve, sha, config_from_recipe


def admitted_splits(manifest, state, feedback, source, pixels, reference):
    if reference.chip_id.duplicated().any() or not set(reference.split) <= {'train', 'validation'}:
        raise ValueError('Temporal reference must have unique development split membership')
    planned = dict(zip(reference.chip_id, reference.split))
    touched = touched_tiles(manifest, state, source, pixels, feedback,
                            intersection_tolerance_pixels=1e-3)
    return {chip: planned[chip] for chip, reasons in touched.items()
            if re.fullmatch(r'r\d{6}_c\d{6}', chip) and chip in planned
            and (planned[chip] == 'train' or 'full-more-done-tile' in reasons)}


def build_admission(config, raster, feedback, inventory):
    import rasterio
    if feedback is None or inventory is None:
        raise ValueError('Temporal training requires frozen curated admission and explicit inventory')
    directory = Path(feedback).parent
    path = directory/'temporal-admission.json'
    if not path.is_file() or load(directory) is None:
        raise ValueError('Temporal acquisition requires a frozen curated admission adapter')
    policy = json.loads(path.read_text())
    if (policy.get('schema_version') != 1 or policy.get('test_evaluated') is not False
            or policy['acquisition_id'] != config.imagery.acquisition_id
            or policy['city'] != config.inventory.city.lower()
            or Path(inventory).resolve() != (directory/'inventory.parquet').resolve()
            or Path(raster).resolve() != resolve(config.paths.root, policy['model_raster']).resolve()
            or config.imagery.boundary_shifts or config.imagery.chip_pixels != 512
            or config.imagery.resolution_m != .3
            or policy['split'] != config.split.model_dump(mode='json') or policy['seed'] != config.seed):
        raise ValueError('Temporal acquisition, imagery, grid or split policy drift')
    for relative, expected in policy['checksums'].items():
        if sha(resolve(config.paths.root, relative)) != expected:
            raise ValueError('Temporal frozen input changed: '+relative)
    manifest, state, published = frozen_context(directory)
    reference = pd.read_parquet(directory/'reference-chips.parquet')
    with rasterio.open(resolve(config.paths.root, policy['source_raster'])) as original, rasterio.open(raster) as source:
        if (source.crs != original.crs or source.bounds != original.bounds
                or source.width != original.width*2 or source.height != original.height*2
                or source.res != (.3, .3) or source.count != original.count or source.count != 4):
            raise ValueError('Temporal model raster does not preserve the review footprint')
        admitted = admitted_splits(manifest, state, published, source, 512, reference)
    if admitted != policy['candidates']:
        raise ValueError('Temporal curation admission changed')
    excluded = set(policy['excluded_nodata'])
    admitted = {chip: split for chip, split in admitted.items() if chip not in excluded}
    if not admitted or admitted != policy['admitted']:
        raise ValueError('Temporal admitted cohort is empty or changed')
    return admitted


def verify_dataset(directory, config):
    directory = Path(directory)
    frozen = config.paths.root/'run-inputs'/config.experiment/config.imagery.acquisition_id
    expected = build_admission(config, config.imagery.local_raster,
                               frozen/'training-feedback.json', frozen/'inventory.parquet')
    frame = pd.read_parquet(directory/'chips.parquet')
    if frame.chip_id.duplicated().any() or dict(zip(frame.chip_id, frame.split)) != expected:
        raise ValueError('Temporal materialized coverage differs from frozen admission')
    labels = pd.read_parquet(directory/'labels.parquet')
    if not set(zip(labels.chip_id, labels.split)) <= set(expected.items()):
        raise ValueError('Temporal labels cross frozen cohort membership')
    receipt = json.loads((directory/'curation-admission.json').read_text())
    supervised = {item['chip_id']: item['supervised_cells'] for item in receipt['materialized']}
    if set(supervised) != set(expected) or any(value <= 0 for value in supervised.values()):
        raise ValueError('Temporal admitted tiles require actual trusted supervision')
    return frame.groupby('split').size().to_dict()


def build_sources(root, experiment, reference, warm, run_dir):
    from urban_tree_ml.chips import build_chips
    from urban_tree_ml.warm_recipe import apply_warm_recipe
    from urban_tree_ml.config import TargetsConfig
    inputs = root/'run-inputs'/experiment
    path = inputs/'temporal-joint.json'
    if not path.exists():
        return [], {}
    plan = json.loads(path.read_text())
    if plan.get('experiment') != experiment or plan.get('test_evaluated') is not False:
        raise ValueError('Temporal joint identity or test policy differs')
    configs, sources, audit = [], {}, {}
    for key, raw in plan['configs'].items():
        config = config_from_recipe(root, raw, experiment)
        if config.imagery.acquisition_id != key:
            raise ValueError('Temporal config key differs from acquisition')
        config.reference = reference
        apply_warm_recipe(config, warm)
        center = json.loads((inputs/'center-policy.json').read_text())
        config.targets = TargetsConfig.model_validate({**config.targets.model_dump(), **center})
        frozen = inputs/key
        dataset = experiment+'-'+key
        destination = root/'chips'/dataset
        if destination.exists():
            raise ValueError('Temporal dataset already exists')
        print('Building curated acquisition '+key, flush=True)
        build_chips(config, config.imagery.local_raster, feedback_path=frozen/'training-feedback.json',
                    feedback_source_raster=frozen/'imagery.vrt', inventory_source=frozen/'inventory.parquet',
                    output_dataset=dataset)
        config.dataset = dataset
        audit[key] = verify_dataset(destination, config)
        (destination/'temporal-dataset.json').write_text(json.dumps(
            {'config': config.model_dump(mode='json')}, indent=2)+'\n')
        (run_dir/f'config-{key}.json').write_text(config.model_dump_json(indent=2))
        configs.append(config)
        sources[key] = destination
    (run_dir/'temporal-curated-admission.json').write_text(json.dumps(audit, indent=2)+'\n')
    return configs, sources
