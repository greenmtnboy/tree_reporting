"""Read-only neighboring inventory, scoped to one acquisition and development split.

Context markers never enter the editable sample list or grant label admission.
Prepared trees link to their original editor; unprepared inventory remains advisory.
"""
import json
import math
from pathlib import Path

import numpy as np
import polars as pl

from urban_tree_ml.tree_curation import transformers
from urban_tree_ml.splits import Block, split_for_block, _is_guarded

_cache = {}


def _source(context, manifest):
    inventory = Path(manifest['metadata'].get('inventory', ''))
    receipt = inventory.parent / 'review-inventory-source.json'
    if receipt.is_file():
        source = Path(json.loads(receipt.read_text(encoding='utf-8'))['source'])
        if source.is_file():
            return source, True
    source = context.config.paths.root / 'inventory' / context.city / 'inventory.parquet'
    if source.is_file():
        return source, True
    return (inventory if inventory.is_file() else None), False


def _catalog(context, manifest, state):
    source, complete = _source(context, manifest)
    signature = (str(source), source.stat().st_mtime_ns, source.stat().st_size) if source else None
    key = (str(context.directory), id(manifest), state['state_revision'], signature)
    if key in _cache:
        return _cache[key]
    rows = {}
    if source:
        frame = pl.scan_parquet(source)
        fields = ['tree_id', 'longitude', 'latitude', 'species', 'diameter_at_breast_height', 'plant_date']
        fields = [field for field in fields if field in frame.collect_schema()]
        for row in frame.select(fields).collect().iter_rows(named=True):
            rows[str(row['tree_id'])] = dict(row, dbh_in=row.get('diameter_at_breast_height'))
    owners = {s['scene_id']: s for s in manifest['scenes']}
    for sample in manifest['samples']:
        # Never expose sealed-test samples or borrow labels from another acquisition.
        if sample.get('split') not in {'train', 'validation'} or sample.get('reference_only'):
            continue
        tid = str(sample['tree_id'])
        row = rows.setdefault(tid, {})
        row.update({k: sample.get(k) for k in ['tree_id', 'longitude', 'latitude', 'species', 'dbh_in', 'split']})
        if sample.get('scene_id') in owners:
            row.update(owner_scene_id=sample['scene_id'], owner_chip_id=owners[sample['scene_id']].get('validation_chip_id'))
        review = state['reviews'].get(sample['sample_id'], {})
        row.update(status=review.get('status', 'unreviewed'), radius_m=review.get('crown_radius_m'),
                   east_m=review.get('east_m', 0), north_m=review.get('north_m', 0))
        if review.get('species_override'):
            row['species'] = review['species_override']
    records = [row for row in rows.values() if all(isinstance(row.get(k), (int, float)) and math.isfinite(row[k]) for k in ('longitude', 'latitude'))]
    project = transformers(manifest['metadata']['curation_crs'])[0]
    x, y = project.transform([r['longitude'] for r in records], [r['latitude'] for r in records])
    points = np.column_stack([x, y])
    for i, row in enumerate(records):
        points[i] += [row.get('east_m', 0), row.get('north_m', 0)]
    result = records, points, complete
    _cache[key] = result
    while len(_cache) > 2:
        _cache.pop(next(iter(_cache)))
    return result


def neighboring_inventory(context, manifest, state, scene_id):
    from urban_tree_ml.review_gallery import scoped_manifest
    from urban_tree_ml.review_context import CONTEXT_PIXELS

    scoped = scoped_manifest(manifest, scene_id)
    scene = scoped['scenes'][0]
    anchor = scene.get('anchor') or next(iter(scoped['samples']), None)
    if not anchor or manifest['metadata'].get('curation_schema_version') != 2:
        return {'trees': [], 'available': False}
    records, points, available = _catalog(context, manifest, state)
    project = transformers(manifest['metadata']['curation_crs'])[0]
    ax, ay = project.transform(anchor['longitude'], anchor['latitude'])
    basis = np.array([[anchor['transform_a'], anchor['transform_b']],
                      [anchor['transform_d'], anchor['transform_e']]])
    inverse = np.linalg.inv(basis)
    origin = np.array([ax, ay]) - basis @ [anchor['target_x'], anchor['target_y']]
    w, h, pad = scene['image_width'], scene['image_height'], CONTEXT_PIXELS
    center = origin + basis @ [w/2, h/2]
    # Include measured crowns crossing the border, with the same 500 m storage cap.
    reach = np.linalg.norm(basis, 2) * math.hypot(w/2+pad, h/2+pad) + 500
    owned = {str(s['tree_id']) for s in scoped['samples']}
    config = context.config.split
    split_project = transformers(config.projected_crs)[0]
    cutoff = context.config.imagery.planting_date_cutoff
    result = []
    nearby = np.flatnonzero(np.sum((points-center)**2, axis=1) <= reach**2)
    for i in nearby:
        record = records[i]
        if str(record['tree_id']) in owned:
            continue
        planted = record.get('plant_date')
        if cutoff and planted and str(planted)[:10] > str(cutoff)[:10]:
            continue
        sx, sy = split_project.transform(record['longitude'], record['latitude'])
        block = Block(math.floor(sx/config.block_size_m), math.floor(sy/config.block_size_m))
        split = split_for_block(block, config, context.config.seed)
        if split == 'test' or _is_guarded(sx, sy, block, split, config, context.config.seed):
            continue
        px, py = inverse @ (points[i] - origin)
        radius = record.get('radius_m') or 0
        rx, ry = radius * np.linalg.norm(inverse, axis=1)
        if not (-pad-rx <= px <= w+pad+rx and -pad-ry <= py <= h+pad+ry):
            continue
        result.append({**{k: record.get(k) for k in ['tree_id', 'species', 'dbh_in', 'status', 'radius_m', 'owner_scene_id', 'owner_chip_id']},
                       'x': float(px), 'y': float(py), 'split': record.get('split') or split})
    return {'trees': sorted(result, key=lambda r: str(r['tree_id'])), 'available': available}
