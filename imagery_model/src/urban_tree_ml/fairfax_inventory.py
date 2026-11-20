"""Seed inventory-backed Fairfax review windows while preserving curated admission."""
import json
import math
import os
from pathlib import Path

import numpy as np

from urban_tree_ml.mosaic_coverage import source_rectangles, valid_window
import pandas as pd
from pyproj import Transformer
from shapely import covers, points
from shapely.geometry import box
from shapely.ops import transform

from urban_tree_ml.config import load_config
from urban_tree_ml.fairfax_curation import RUN
from urban_tree_ml.fairfax_pilot import load_bundle, save_new, sha256
from urban_tree_ml.inventory import _add_source_label_eligibility, planted_after_imagery
from urban_tree_ml.splits import Block, assign_spatial_splits, split_for_block
from urban_tree_ml.targets import PointLabel, point_label_output_cell
from urban_tree_ml.taxonomy import Taxonomy, encode_taxonomy


def inventory_windows(frame, boundary, source, config):
    """Use county-located source points and whole tiles inside guarded development blocks.

    Border tiles may extend into the neighboring jurisdiction. Reference rows retain
    their publisher identities. Test blocks and cross-split edge pixels are excluded.
    """
    from rasterio.windows import Window, bounds

    frame = frame[covers(boundary, points(frame.longitude, frame.latitude))].copy()
    counts = {'within_county': len(frame)}
    later = planted_after_imagery(frame, config.imagery.planting_date_cutoff)
    counts['planted_after_imagery'] = int(later.sum())
    frame = frame[~later].copy()
    xs, ys = Transformer.from_crs(4326, source.crs, always_xy=True).transform(
        frame.longitude.to_numpy(), frame.latitude.to_numpy())
    cols, rows = (~source.transform) * (xs, ys)
    frame['pixel_col'], frame['pixel_row'] = cols, rows
    pixels = config.imagery.chip_pixels
    frame['row_offset'] = (rows // pixels * pixels).astype(int)
    frame['column_offset'] = (cols // pixels * pixels).astype(int)
    projected = Transformer.from_crs(source.crs, config.split.projected_crs, always_xy=True).transform
    size, guard = config.split.block_size_m, config.split.guard_m
    rectangles = source_rectangles(getattr(source, "name", None))
    windows, selected = [], []
    for (row, col), group in frame.groupby(['row_offset', 'column_offset'], sort=True):
        window = Window(col, row, pixels, pixels)
        footprint = transform(projected, box(*bounds(window, source.transform)).segmentize(25))
        center = footprint.centroid
        block = Block(math.floor(center.x / size), math.floor(center.y / size))
        split = split_for_block(block, config.split, config.seed)
        reason = None
        if split == 'test':
            reason = 'test_block'
        elif not box(block.x * size + guard, block.y * size + guard,
                     (block.x + 1) * size - guard, (block.y + 1) * size - guard).covers(footprint):
            reason = 'block_guard'
        elif row < 0 or col < 0 or row + pixels > source.height or col + pixels > source.width:
            reason = 'outside_raster'
        if reason:
            counts[reason] = counts.get(reason, 0) + len(group)
            continue
        valid = float(valid_window(source, config.imagery.bands, window, rectangles).mean())
        if valid < config.imagery.minimum_valid_fraction:
            counts['invalid_imagery'] = counts.get('invalid_imagery', 0) + len(group)
            continue
        chip = f'r{row // pixels:06d}_c{col // pixels:06d}'
        windows.append(dict(row=int(row), col=int(col), split=split, block_x=block.x,
                            block_y=block.y, valid_fraction=valid, inventory_trees=len(group)))
        selected.append(group.assign(chip_id=chip, split=split))
    if not selected:
        raise ValueError('No inventory-backed development windows fit the existing split guards')
    result = pd.concat(selected, ignore_index=True)
    counts['selected_trees'], counts['selected_tiles'] = len(result), len(windows)
    return result, windows, counts


def reference_labels(inventory, config):
    records = []
    for row in inventory.itertuples():
        cell = point_label_output_cell(PointLabel(row.pixel_col - row.column_offset,
                                                 row.pixel_row - row.row_offset),
                                       image_height=512, image_width=512,
                                       stride=config.targets.output_stride)
        if cell is None:
            raise ValueError('Reference label falls outside its review window')
        records.append(dict(tree_id=row.tree_id, split=row.split, chip_id=row.chip_id,
                            longitude=row.longitude, latitude=row.latitude,
                            pixel_col=row.pixel_col, pixel_row=row.pixel_row,
                            output_x=cell[0], output_y=cell[1], dbh_log1p=row.dbh_log1p,
                            genus_id=row.genus_id, species_id=row.species_id,
                            crown_radius_m=np.nan, crown_source=None, vegetation_type='',
                            source_city=row.city, data_source=row.data_source))
    return pd.DataFrame(records)


def prepare(root, bundle, name, checkpoint):
    import rasterio
    from urban_tree_ml.prediction_chips import build_prediction_chips

    root, bundle, checkpoint = Path(root).resolve(), Path(bundle).resolve(), Path(checkpoint).resolve()
    if not name or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-' for c in name):
        raise ValueError('Review name must contain lowercase letters, digits and hyphens')
    base = root / 'run-inputs' / RUN
    config = load_config(base / 'config.json')
    if config.imagery.chip_pixels != 512:
        raise ValueError('Fairfax review requires the existing 512px grid')
    manifest, paths, boundary = load_bundle(bundle)
    if paths['calibration_inventory'] is None:
        raise ValueError('Calibration inventory is unavailable')
    source = pd.read_parquet(paths['calibration_inventory'])
    with rasterio.open(config.imagery.local_raster) as raster:
        inventory, windows, counts = inventory_windows(source, boundary, raster, config)
    old_plan = json.loads((base / 'sample-plan.json').read_text())
    if {(w['row'], w['col']) for w in old_plan['windows']} & {(w['row'], w['col']) for w in windows}:
        raise ValueError('Existing review windows overlap new inventory tiles')
    if inventory.tree_id.duplicated().any():
        raise ValueError('Source tree IDs must be globally distinct in the review workspace')
    taxonomy_file = checkpoint.parent.parent / 'vegetation-taxonomy.json'
    taxonomy = Taxonomy(**json.loads(taxonomy_file.read_text()))
    inventory = encode_taxonomy(_add_source_label_eligibility(inventory, config.inventory), taxonomy)
    spatial = assign_spatial_splits(inventory, config.split, config.seed)
    if not spatial.split.eq(inventory.split).all() or not spatial.split_eligible.all():
        raise ValueError('Inventory points disagree with guarded tile splits')
    inventory['split_eligible'] = True
    inventory['genus_eligible'] = inventory.taxon_eligible & inventory.genus_id.ge(0)
    inventory['species_eligible'] = inventory.taxon_eligible & inventory.species_id.ge(0)
    inventory['dbh_log1p'] = np.nan
    inventory.loc[inventory.dbh_eligible, 'dbh_log1p'] = np.log1p(inventory.loc[inventory.dbh_eligible, 'diameter_at_breast_height'])
    destination = root / 'run-inputs' / name
    destination.mkdir(exist_ok=False)
    config.experiment, config.dataset = name, name + '-usffx'
    config.reference.taxonomy_path = taxonomy_file
    config.training.accelerator, config.training.workers = 'cpu', 0
    config.evaluation.confidence_threshold = .13
    save_new(destination / 'config.json', config.model_dump(mode='json'))
    inventory.to_parquet(destination / 'inventory.parquet', index=False)
    reference_labels(inventory, config).to_parquet(destination / 'reference-labels.parquet', index=False)
    # Keep the raw prediction dataset unlabeled and training-ineligible. Reference
    # labels are attached only to the separate, completed review evaluation.
    build_prediction_chips(config, config.imagery.local_raster,
                           [(w['row'], w['col']) for w in windows],
                           labels_template=root / 'chips' / (RUN + '-usffx') / 'labels.parquet',
                           window_splits={(w['row'], w['col']): w['split'] for w in windows})
    save_new(destination / 'sample-plan.json', dict(windows=windows, seed=config.seed,
                                                  split_policy=config.split.model_dump(mode='json')))
    dataset = root / 'chips' / config.dataset
    receipt = dict(run=name, counts=counts, source_manifest_sha256=sha256(bundle / 'manifest.json'),
                   source_inventory_sha256=manifest['sha256'][manifest['calibration_inventory']],
                   checkpoint=str(checkpoint), checkpoint_sha256=sha256(checkpoint),
                   base_plan_sha256=sha256(base / 'sample-plan.json'),
                   raster_sha256=sha256(config.imagery.local_raster),
                   dataset_sha256={str(p.relative_to(root)): sha256(p) for p in dataset.rglob('*') if p.is_file()},
                   limitation='Sparse neighboring-publisher records within Fairfax County; border tiles can cross the county line. Falls Church inventory dates to 2004.',
                   sha256={p.name: sha256(p) for p in destination.iterdir() if p.is_file()})
    save_new(destination / 'preparation-receipt.json', receipt)
    return receipt


def evaluate(root, name):
    import torch
    from urban_tree_ml.evaluation import run_evaluation, _greedy_matches, unscored_metrics

    root = Path(root).resolve()
    inputs = root / 'run-inputs' / name
    receipt = json.loads((inputs / 'preparation-receipt.json').read_text())
    for path, digest in receipt['sha256'].items():
        if sha256(inputs / path) != digest:
            raise ValueError('Prepared review input changed')
    for path, digest in receipt['dataset_sha256'].items():
        if sha256(root / path) != digest:
            raise ValueError('Prepared review dataset changed')
    if sha256(receipt['checkpoint']) != receipt['checkpoint_sha256']:
        raise ValueError('Checkpoint changed')
    config = load_config(inputs / 'config.json')
    run = root / 'runs' / name
    run.mkdir(exist_ok=False)
    torch.set_num_threads(2)
    labels = pd.read_parquet(inputs / 'reference-labels.parquet')
    for split in sorted(labels.split.unique()):
        cohort = split + '-usffx'
        result = run_evaluation(config, receipt['checkpoint'], split=split, device_name='cpu',
                                cohort=cohort, output_run=run)
        directory = run / 'evaluation' / cohort
        truth = labels[labels.split.eq(split)].reset_index(drop=True)
        predictions = pd.read_parquet(directory / 'predictions.parquet')
        if not predictions.detection_mask_value.eq(0).all():
            raise ValueError('Unreviewed background must remain unsupervised')
        truth.to_parquet(directory / 'ground-truth.parquet', index=False)
        matches = []
        for radius in config.evaluation.match_radii_m:
            matched = _greedy_matches(predictions[predictions.score.ge(config.evaluation.confidence_threshold)], truth,
                                      radius_output_px=radius / (config.imagery.resolution_m * config.targets.output_stride))
            metrics = unscored_metrics()
            metrics['detection'].update(true_positive=len(matched), false_negative=len(truth) - len(matched),
                                        recall=len(matched) / len(truth))
            result['metrics_by_match_radius_m'][str(radius)] = metrics
            matches.extend(dict(radius_m=radius, prediction_index=p, tree_id=str(truth.loc[t, 'tree_id']),
                                distance_m=d * config.imagery.resolution_m * config.targets.output_stride)
                           for p, t, d in matched)
        pd.DataFrame(matches, columns=['radius_m', 'prediction_index', 'tree_id', 'distance_m']).to_parquet(directory / 'matches.parquet', index=False)
        result.update(prediction_only=False, label_status='partial_inventory', ground_truth_trees=len(truth),
                      reference_limitations=receipt['limitation'])
        (directory / 'metrics.json').write_text(json.dumps(result, indent=2))
        metadata = json.loads((directory / 'evaluation-metadata.json').read_text())
        metadata.update(prediction_only=False, label_status='partial_inventory', reference_limitations=receipt['limitation'])
        (directory / 'evaluation-metadata.json').write_text(json.dumps(metadata, indent=2))
    save_new(run / 'inventory-review-receipt.json', receipt)
    (run / 'COMPLETE').write_text('Inventory-backed review inference complete; full-tile curation required for training.\n')
    return {'run': name, 'reference_trees': len(labels), 'tiles': labels.chip_id.nunique()}


def publish(root, name, server):
    """Install additive source rows, then use the server's locked scene-append route."""
    import requests

    root = Path(root).resolve()
    inputs = root / 'run-inputs' / name
    if not (root / 'runs' / name / 'COMPLETE').exists():
        raise ValueError('Review inference is incomplete')
    receipt = json.loads((inputs / 'preparation-receipt.json').read_text())
    base = root / 'run-inputs' / RUN
    for filename, digest in receipt['sha256'].items():
        if sha256(inputs / filename) != digest:
            raise ValueError('Prepared review input changed')
    if sha256(base / 'sample-plan.json') != receipt['base_plan_sha256']:
        raise ValueError('Base development plan changed')
    additions = pd.read_parquet(inputs / 'inventory.parquet')
    if sha256(inputs / 'inventory.parquet') != receipt['sha256']['inventory.parquet']:
        raise ValueError('Prepared inventory changed')
    path = root / 'inventory/usffx/inventory.parquet'
    existing = pd.read_parquet(path)
    overlap = existing[existing.tree_id.isin(additions.tree_id)]
    if not overlap.empty:
        pd.testing.assert_frame_equal(overlap[additions.columns].sort_values('tree_id').reset_index(drop=True),
                                      additions.sort_values('tree_id').reset_index(drop=True), check_dtype=False)
    else:
        merged = additions if existing.empty else pd.concat([existing, additions], ignore_index=True)
        temporary = path.with_suffix('.parquet.part')
        merged.to_parquet(temporary, index=False)
        os.replace(temporary, path)
    # The extension is immutable and audited independently from the original plan.
    extension = dict(base_plan_sha256=receipt['base_plan_sha256'],
                     inventory_sha256=sha256(path),
                     **json.loads((inputs / 'sample-plan.json').read_text()))
    extension_path = base / 'development-extensions' / (name + '.json')
    if extension_path.exists():
        if json.loads(extension_path.read_text()) != extension:
            raise ValueError('Published development extension changed')
    else:
        save_new(extension_path, extension)
    manifest_path = root / 'qa/registration' / RUN / 'manifest.json'
    known = {scene.get('validation_chip_id') for scene in json.loads(manifest_path.read_text())['scenes']}
    for row in additions[['chip_id', 'split']].drop_duplicates().itertuples():
        if row.chip_id in known:
            continue
        response = requests.get(server.rstrip('/') + '/curate', params=dict(
            run=name + '::' + row.split + '-usffx', chip=row.chip_id, threshold=.13, radius=2),
            allow_redirects=False, timeout=60)
        if response.status_code != 303:
            raise RuntimeError(f'Curator rejected inventory tile {row.chip_id}: {response.status_code} {response.text[:500]}')
    queue = root / 'benchmarks' / ('zz-' + name)
    if not (queue / 'COMPLETE').exists():
        queue.mkdir(exist_ok=True)
        target = queue / 'validation-targets.csv'
        pd.DataFrame([dict(city='usffx', chip_id=row.chip_id, queue='Fairfax inventory ' + row.split,
                           reason='Check sparse upstream inventory against imagery; full-tile more_done required')
                      for row in additions[['chip_id', 'split']].drop_duplicates().itertuples()]).to_csv(target, index=False)
        save_new(queue / 'manifest.json', dict(new_run=name, output_sha256={target.name: sha256(target)}))
        (queue / 'COMPLETE').write_text('Inventory review assignments ready\n')
    return {'run': name, 'tiles': additions.chip_id.nunique(), 'trees': len(additions),
            'sources': additions.data_source.value_counts().to_dict()}
