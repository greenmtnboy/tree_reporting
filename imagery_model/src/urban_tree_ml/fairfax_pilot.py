"""Boundary-driven Fairfax prediction pilot and upstream source-bundle audit."""
from __future__ import annotations

import hashlib
import json
import math
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np

from urban_tree_ml.mosaic_coverage import source_rectangles, valid_window
import pandas as pd
import requests
from pyproj import Transformer
from shapely.geometry import box, shape
from shapely.ops import transform, unary_union
from shapely.prepared import prep

from urban_tree_ml.config import ProjectConfig
from urban_tree_ml.inventory import INVENTORY_COLUMNS
from urban_tree_ml.species_coverage import species_coverage
from urban_tree_ml.splits import Block, split_for_block

PARENT = 'sf-boston-nyc-naip-swin-30cm-v17-curated-finetune'
CHECKPOINT_SHA = '77b3c2db89a439a4d4aa51e9df658ea0cf80f79bb3b249ba86754830597711df'
STAC = 'https://planetarycomputer.microsoft.com/api/stac/v1/search'
REGION_BBOX = [-77.54, 38.59, -77.04, 39.00]


def sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def save_new(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write('\n')


def discover_catalog(years, output):
    """Save candidate metadata only. County clipping and acquisition remain explicit."""
    if Path(output).exists():
        raise FileExistsError(output)
    items = {}
    for year in sorted(set(years)):
        url = STAC
        params = {'collections': 'naip', 'bbox': ','.join(map(str, REGION_BBOX)),
                  'datetime': f'{year}-01-01T00:00:00Z/{year}-12-31T23:59:59Z', 'limit': 100}
        visited = set()
        while url:
            response = requests.get(url, params=params, timeout=60)
            response.raise_for_status()
            if response.url in visited:
                raise ValueError('Repeated STAC pagination URL')
            visited.add(response.url)
            page = response.json()
            for item in page['features']:
                items[item['id']] = item
            links = [link for link in page.get('links', []) if link['rel'] == 'next']
            if len(links) > 1 or any(link.get('method', 'GET') != 'GET' for link in links):
                raise ValueError('Unsupported STAC pagination response')
            url = links[0]['href'] if links else None
            params = None
    if not items:
        raise ValueError('No candidate imagery found')
    result = {'created_at': datetime.now(timezone.utc).isoformat(), 'bbox': REGION_BBOX,
              'years': sorted(set(years)), 'selection_status': 'candidate_metadata_only',
              'items': [items[key] for key in sorted(items)]}
    save_new(output, result)
    return {'output': str(output), 'candidate_items': len(items),
            'virginia_items': sum(key.startswith('va_') for key in items)}


def load_bundle(directory):
    directory = Path(directory).resolve()
    manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8-sig'))
    if manifest['schema_version'] != 1 or manifest['city'] != 'USFFX':
        raise ValueError('Expected schema 1 and Fairfax County code USFFX')
    if not manifest.get('sources') or not isinstance(manifest.get('limitations'), list):
        raise ValueError('Bundle needs sources and explicit limitations')
    for source in manifest['sources']:
        if any(not source.get(key) for key in
               ('name', 'url', 'retrieved_at', 'jurisdiction', 'license', 'coverage')):
            raise ValueError('Incomplete source provenance')
    paths = {}
    for key in ('boundary', 'inventory', 'calibration_inventory'):
        relative = manifest[key]
        if relative is None and key != 'boundary':
            paths[key] = None
            continue
        path = (directory / relative).resolve()
        if Path(relative).is_absolute() or not path.is_relative_to(directory):
            raise ValueError(f'Bundle path escapes its directory: {key}')
        if sha256(path) != manifest['sha256'].get(relative):
            raise ValueError(f'Bundle checksum mismatch: {key}')
        paths[key] = path
    geojson = json.loads(paths['boundary'].read_text(encoding='utf-8-sig'))
    if geojson['type'] == 'FeatureCollection':
        geometries = [shape(feature['geometry']) for feature in geojson['features']]
    elif geojson['type'] == 'Feature':
        geometries = [shape(geojson['geometry'])]
    else:
        geometries = [shape(geojson)]
    if not geometries or any(g.geom_type not in {'Polygon', 'MultiPolygon'} or
                             g.is_empty or not g.is_valid for g in geometries):
        raise ValueError('County boundary must contain valid polygons')
    boundary = unary_union(geometries)
    if not box(-180, -90, 180, 90).covers(boundary):
        raise ValueError('Boundary coordinates must be WGS84')
    return manifest, paths, boundary


def audit_bundle(directory, taxonomy_path):
    manifest, paths, boundary = load_bundle(directory)
    taxonomy = json.loads(Path(taxonomy_path).read_text(encoding='utf-8-sig'))
    audits = {}
    for kind in ('inventory', 'calibration_inventory'):
        if paths[kind] is None:
            audits[kind] = {'status': 'unavailable'}
            continue
        frame = pd.read_parquet(paths[kind])
        missing = set(INVENTORY_COLUMNS) - set(frame.columns)
        if missing:
            raise ValueError(f'{kind} missing columns: {sorted(missing)}')
        for key in ('tree_id', 'city', 'data_source'):
            if not frame[key].map(lambda value: isinstance(value, str) and bool(value.strip())).all():
                raise ValueError(f'{kind} needs nonempty string {key}')
        if frame.duplicated(['city', 'data_source', 'tree_id']).any():
            raise ValueError(f'Duplicate source tree identities in {kind}')
        if not frame.city.str.fullmatch(r'US[A-Z]{3}').all():
            raise ValueError(f'Invalid jurisdiction codes in {kind}')
        if not frame.species.dropna().map(lambda value: isinstance(value, str)).all():
            raise ValueError(f'Species names must be strings in {kind}')
        dbh = pd.to_numeric(frame.diameter_at_breast_height.dropna(), errors='raise')
        if not np.isfinite(dbh).all() or not dbh.gt(0).all():
            raise ValueError(f'DBH must be positive inches or null in {kind}')
        pd.to_datetime(frame.plant_date.dropna(), errors='raise', format='mixed', utc=True)
        coordinates = frame[['longitude', 'latitude']].to_numpy(dtype=float)
        if not np.isfinite(coordinates).all() or not (
            frame.longitude.between(-180, 180) & frame.latitude.between(-90, 90)
        ).all():
            raise ValueError(f'Invalid coordinates in {kind}')
        from shapely import points, covers
        within = covers(boundary, points(coordinates))
        if kind == 'inventory' and (not frame.city.eq(manifest['city']).all() or not within.all()):
            raise ValueError('County inventory has other jurisdictions or out-of-boundary records')
        groups = []
        for (city, source), rows in frame.groupby(['city', 'data_source'], sort=True):
            groups.append({'city': city, 'data_source': source,
                           'coverage': species_coverage(rows, taxonomy)})
        audits[kind] = {'status': 'audited', 'rows': len(frame),
                        'within_county': int(within.sum()), 'groups': groups}
    return {'schema_version': 1, 'city': manifest['city'], 'parent': PARENT,
            'manifest_sha256': sha256(Path(directory) / 'manifest.json'),
            'taxonomy_sha256': sha256(taxonomy_path), 'boundary_bounds': list(boundary.bounds),
            'inventories': audits, 'limitations': manifest['limitations'],
            'coverage_measure': 'Inventory-weighted vocabulary support; accuracy requires reviewed tiles'}


def select_items(catalog, boundary, year, item_ids):
    """Require an explicit, single-vintage Virginia RGBN acquisition."""
    if not item_ids or len(set(item_ids)) != len(item_ids):
        raise ValueError('Select unique imagery item IDs')
    indexed = {item['id']: item for item in catalog['items']}
    records = []
    for item_id in sorted(item_ids):
        item = indexed[item_id]
        properties, asset = item['properties'], item['assets']['image']
        names = [band.get('common_name', '').lower() for band in asset.get('eo:bands', [])]
        if (not item_id.startswith('va_') or properties['datetime'][:4] != str(year)
                or properties.get('gsd') != .6 or names != ['red', 'green', 'blue', 'nir']):
            raise ValueError(f'Expected same-year Virginia 60cm RGBN imagery: {item_id}')
        if not shape(item['geometry']).intersection(boundary).area > 0:
            raise ValueError(f'Imagery does not intersect the county: {item_id}')
        records.append({'id': item_id, 'collection': 'naip', 'datetime': properties['datetime'],
                        'bbox': item['bbox'], 'asset_key': 'image', 'asset_href': asset['href'],
                        'band_names': names, 'properties': properties})
    return records


def acquire_imagery(bundle, artifacts, catalog_path, year, item_ids, output):
    """Fetch selected assets with existing download/mosaic validators; resume identical requests."""
    from urban_tree_ml.imagery import fetch_stac_item, build_vrt_mosaic

    output, artifacts = Path(output).resolve(), Path(artifacts).resolve()
    _, _, boundary = load_bundle(bundle)
    catalog = json.loads(Path(catalog_path).read_text(encoding='utf-8'))
    records = select_items(catalog, boundary, year, item_ids)
    request = {'catalog_sha256': sha256(catalog_path), 'bundle_manifest_sha256': sha256(Path(bundle) / 'manifest.json'),
               'year': year, 'item_ids': sorted(item_ids)}
    output.mkdir(parents=True, exist_ok=True)
    lock = output / 'acquisition.lock'
    with lock.open('x', encoding='utf-8') as stream:
        stream.write(datetime.now(timezone.utc).isoformat())
    try:
        request_path = output / 'request.json'
        if request_path.exists():
            if json.loads(request_path.read_text()) != request:
                raise ValueError('Acquisition request changed; use a new directory')
        else:
            save_new(request_path, request)
        config = ProjectConfig.model_validate_json((artifacts / 'runs' / PARENT / 'config-usbos.json').read_text())
        config.paths.root = output
        config.inventory.city = 'USFFX'
        config.imagery.source_resolution_m = .6
        config.imagery.resolution_m = .6
        config.imagery.item_ids = sorted(item_ids)
        config.imagery.datetime = str(year)
        config.imagery.local_raster = None
        index = output / 'imagery/usffx/stac-items.json'
        if not index.exists():
            save_new(index, {'items': records})
        elif json.loads(index.read_text()) != {'items': records}:
            raise ValueError('Frozen imagery index changed')
        assets = []
        for item_id in sorted(item_ids):
            record = fetch_stac_item(config, item_id)
            if not record['sha256'] or sha256(record['path']) != record['sha256']:
                raise ValueError(f'Download receipt checksum mismatch: {item_id}')
            assets.append(record)
        mosaic = build_vrt_mosaic(config, str(year))
        result = {'request': request, 'source_vrt': mosaic['path'],
                  'source_vrt_sha256': sha256(mosaic['path']),
                  'acquisition_date': max(item['datetime'][:10] for item in records), 'assets': assets}
        receipt = output / 'acquisition-receipt.json'
        if not receipt.exists():
            save_new(receipt, result)
        else:
            previous = json.loads(receipt.read_text())
            if previous['source_vrt_sha256'] != result['source_vrt_sha256']:
                raise ValueError('Frozen mosaic changed')
        return result
    finally:
        lock.unlink()


def sample_windows(source, boundary, config, counts, *, holdout_only=False):
    """Choose geographically spread full windows without looking at inventory points."""
    from rasterio.windows import Window, bounds
    if source.crs is None or source.transform.b or source.transform.d:
        raise ValueError('Expected a north-up georeferenced raster')
    if source.count < 4 or not all(math.isclose(value, .3, abs_tol=1e-6) for value in source.res):
        raise ValueError('Expected RGBN imagery at model resolution 0.3 m')
    expected = {'test'} if holdout_only else {'train', 'validation'}
    if set(counts) != expected or any(value < 1 for value in counts.values()):
        raise ValueError('Request positive train and validation sample counts')
    projected = Transformer.from_crs(source.crs, config.split.projected_crs, always_xy=True).transform
    county = transform(Transformer.from_crs('EPSG:4326', source.crs, always_xy=True).transform, boundary)
    prepared_county = prep(county)
    candidates = {split: {} for split in counts}
    size, guard = config.split.block_size_m, config.split.guard_m
    pixels = config.imagery.chip_pixels
    for row in range(0, source.height - pixels + 1, pixels):
        for col in range(0, source.width - pixels + 1, pixels):
            window = Window(col, row, pixels, pixels)
            footprint = box(*bounds(window, source.transform))
            if not prepared_county.covers(footprint):
                continue
            footprint_projected = transform(projected, footprint.segmentize(25))
            center = footprint_projected.centroid
            block = Block(math.floor(center.x / size), math.floor(center.y / size))
            split = split_for_block(block, config.split, config.seed)
            interior = box(block.x * size + guard, block.y * size + guard,
                           (block.x + 1) * size - guard, (block.y + 1) * size - guard)
            if split not in candidates or not interior.covers(footprint_projected):
                continue
            rank = hashlib.sha256(f'{config.seed}:{row}:{col}'.encode()).hexdigest()
            candidates[split].setdefault(block, []).append((rank, row, col))
    rectangles = source_rectangles(getattr(source, "name", None))
    selected = []
    available = {}
    for split, blocks in candidates.items():
        available[split] = {'blocks': len(blocks), 'geometric_windows': sum(map(len, blocks.values()))}
        # Round-robin blocks gives broad geographic coverage before taking a second chip per block.
        ordered = sorted(blocks, key=lambda b: hashlib.sha256(f'{config.seed}:{b.x}:{b.y}'.encode()).hexdigest())
        queues = {block: iter(sorted(blocks[block])) for block in ordered}
        accepted = 0
        while queues and accepted < counts[split]:
            for block in list(queues):
                candidate = next(queues[block], None)
                if candidate is None:
                    del queues[block]
                    continue
                _, row, col = candidate
                window = Window(col, row, pixels, pixels)
                valid = float(valid_window(source, config.imagery.bands, window, rectangles).mean())
                if valid < config.imagery.minimum_valid_fraction:
                    continue
                selected.append({'row': row, 'col': col, 'split': split,
                                 'block_x': block.x, 'block_y': block.y, 'valid_fraction': valid})
                accepted += 1
                if accepted == counts[split]:
                    break
        if accepted != counts[split]:
            raise ValueError(f'Insufficient valid {split} windows: {accepted}/{counts[split]}')
    return {'windows': selected, 'available': available, 'sampling': 'seeded block round-robin',
            'split_policy': config.split.model_dump(), 'seed': config.seed}


def prepare_pilot(bundle, artifacts, source_vrt, acquisition_date, output, counts):
    """Create a new isolated prediction-only dataset using the retained model recipe."""
    import rasterio
    from urban_tree_ml.prediction_chips import build_prediction_chips
    from urban_tree_ml.resolution_recipe import finer_vrt

    artifacts, output, source_vrt = Path(artifacts).resolve(), Path(output).resolve(), Path(source_vrt).resolve()
    date.fromisoformat(acquisition_date)
    run = artifacts / 'runs' / PARENT
    checkpoint = run / 'checkpoints' / '000-0000282.ckpt'
    if sha256(checkpoint) != CHECKPOINT_SHA:
        raise ValueError('Retained parent checkpoint checksum mismatch')
    taxonomy = run / 'vegetation-taxonomy.json'
    audit = audit_bundle(bundle, taxonomy)
    manifest, paths, boundary = load_bundle(bundle)
    parent_dataset = artifacts / 'chips' / f'{PARENT}-usbos'
    config = ProjectConfig.model_validate_json((run / 'config-usbos.json').read_text())
    if config.model.backbone != 'swin_tiny' or config.imagery.chip_pixels != 512:
        raise ValueError('Unexpected retained parent architecture')
    # Fresh isolated output allows interrupted preparation to be inspected without overwriting a run.
    output.mkdir(parents=True, exist_ok=False)
    save_new(output / 'source-audit.json', audit)
    model_vrt = output / 'imagery-30cm.vrt'
    native = finer_vrt(source_vrt, model_vrt, .3)
    if any(not all(math.isclose(value, .6, abs_tol=1e-6) for value in values) for values in native.values()):
        raise ValueError('Fairfax pilot expects native 0.6 m Virginia NAIP source TIFFs')
    config.experiment = output.name
    config.dataset = output.name + '-usffx'
    config.paths.root = output
    config.paths.annotations = output / 'annotations'
    config.inventory.city = manifest['city']
    config.inventory.parquet_url = str(paths['inventory'] or output / 'inventory-unavailable.parquet')
    config.split.projected_crs = 'EPSG:32618'
    config.imagery.local_raster = model_vrt
    config.imagery.provider = 'local'
    config.imagery.item_ids = []
    config.imagery.datetime = acquisition_date
    config.imagery.planting_date_cutoff = date.fromisoformat(acquisition_date)
    config.imagery.source_resolution_m = .6
    config.imagery.boundary_shifts = False
    config.reference.taxonomy_path = taxonomy
    config.reference.normalization_path = parent_dataset / 'normalization.json'
    config.training.accelerator = 'cpu'
    config.training.workers = 0
    with rasterio.open(model_vrt) as source:
        plan = sample_windows(source, boundary, config, counts)
    save_new(output / 'sample-plan.json', plan)
    windows = [(item['row'], item['col']) for item in plan['windows']]
    splits = {(item['row'], item['col']): item['split'] for item in plan['windows']}
    summary = build_prediction_chips(config, model_vrt, windows,
                                     labels_template=parent_dataset / 'labels.parquet', window_splits=splits)
    save_new(output / 'config.json', config.model_dump(mode='json'))
    chip_root = output / 'chips' / config.dataset
    input_paths = [output / 'config.json', output / 'sample-plan.json', taxonomy,
                   chip_root / 'chips.parquet', chip_root / 'labels.parquet', chip_root / 'normalization.json',
                   chip_root / 'summary.json']
    input_paths.extend(chip_root / path for path in pd.read_parquet(chip_root / 'chips.parquet').path)
    receipt = {'status': 'prepared_prediction_only', 'parent': PARENT,
               'checkpoint': str(checkpoint), 'checkpoint_sha256': CHECKPOINT_SHA,
               'source_vrt': str(source_vrt), 'source_vrt_sha256': sha256(source_vrt),
               'source_files': [{'path': path, 'sha256': sha256(path)} for path in sorted(native)],
               'source_resolution_m': .6, 'model_resolution_m': .3,
               'acquisition_date': acquisition_date, 'bundle_manifest_sha256': audit['manifest_sha256'],
               'config_sha256': sha256(output / 'config.json'),
               'sample_plan_sha256': sha256(output / 'sample-plan.json'), 'dataset': summary,
               'inference_inputs': [{'path': str(path), 'sha256': sha256(path)} for path in input_paths],
               'next_step': 'Run retained-checkpoint inference, then register the explicit curator cohort',
               'label_policy': 'Unreviewed areas have zero detection supervision; full-tile review required'}
    save_new(output / 'preparation-receipt.json', receipt)
    (output / 'COMPLETE').write_text('Prediction preparation complete\n', encoding='utf-8')
    return receipt


def predict_pilot(pilot):
    """Run the retained checkpoint on CPU into new, explicitly unscored cohorts."""
    import torch
    from urban_tree_ml.config import load_config
    from urban_tree_ml.evaluation import run_evaluation

    pilot = Path(pilot).resolve()
    if not (pilot / 'COMPLETE').is_file():
        raise ValueError('Prediction preparation is incomplete')
    receipt = json.loads((pilot / 'preparation-receipt.json').read_text())
    if receipt['checkpoint_sha256'] != CHECKPOINT_SHA or sha256(receipt['checkpoint']) != CHECKPOINT_SHA:
        raise ValueError('Retained checkpoint changed')
    for item in receipt['inference_inputs']:
        if sha256(item['path']) != item['sha256']:
            raise ValueError(f'Prepared inference input changed: {item["path"]}')
    config = load_config(pilot / 'config.json')
    chip_root = config.paths.root / 'chips' / config.dataset
    manifest = pd.read_parquet(chip_root / 'chips.parquet')
    if set(manifest.split) != {'train', 'validation'}:
        raise ValueError('Expected train and validation review cohorts only')
    output = pilot / 'runs' / config.experiment
    output.mkdir(parents=True, exist_ok=False)
    # Keep the local review pilot responsive alongside the curator and backups.
    torch.set_num_threads(min(2, torch.get_num_threads()))
    results = []
    for split in ('train', 'validation'):
        result = run_evaluation(config, receipt['checkpoint'], split=split, device_name='cpu',
                                cohort=f'{split}-usffx', output_run=output)
        if (not result['prediction_only'] or result['ground_truth_trees'] != 0
                or result['chips'] != int(manifest.split.eq(split).sum())):
            raise ValueError('Prediction-only inference coverage mismatch')
        results.append(result)
    save_new(output / 'inference-receipt.json', {'checkpoint_sha256': CHECKPOINT_SHA,
                                               'cpu_threads': torch.get_num_threads(), 'cohorts': results})
    (output / 'COMPLETE').write_text('Both prediction-only cohorts verified\n', encoding='utf-8')
    return {'output': str(output), 'cohorts': results}
