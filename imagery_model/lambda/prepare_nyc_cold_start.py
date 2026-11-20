"""Freeze NYC inventory and imagery selection for inference, never training.

Central Park is explicitly unlabeled. Its boundary is a coverage request,
not a ground-truth annotation or an empty/background training label.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

from urban_tree_ml.species_coverage import species_coverage

PARENT = 'sf-boston-naip-swin-30cm-v7-finetune'
RUN = 'nyc-naip-swin-30cm-v7-cold-start-v1'
INVENTORY = 'https://storage.googleapis.com/trilogy_public_models/duckdb/trees/usnyc_tree_info_v2.parquet'
PARKS = 'https://data.cityofnewyork.us/resource/enfh-gkve.json'
STAC = 'https://planetarycomputer.microsoft.com/api/stac/v1'
CENTERS = {'manhattan': (-73.9654, 40.7829), 'bronx': (-73.883, 40.856),
           'queens': (-73.821, 40.749), 'brooklyn': (-73.970, 40.665),
           'staten-island': (-74.150, 40.604)}


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')


def prepare(root):
    target = root / 'run-inputs' / RUN
    target.mkdir(parents=True, exist_ok=True)
    snapshot = target / 'published-inventory.parquet'
    if not snapshot.exists():
        pd.read_parquet(INVENTORY).to_parquet(snapshot, index=False)
    published = pd.read_parquet(snapshot)
    municipal = published[published.data_source.eq('NYC_OPENDATA')].copy()
    if municipal.empty or not municipal.tree_id.is_unique:
        raise ValueError('Expected nonempty municipal inventory with unique tree IDs')
    municipal.to_parquet(target / 'municipal-inventory.parquet', index=False)
    taxonomy_path = root / 'runs' / PARENT / 'evaluation/validation/taxonomy.json'
    audit = species_coverage(municipal, json.loads(taxonomy_path.read_text()))
    audit.update(source=INVENTORY, data_source='NYC_OPENDATA', parent=PARENT,
                 snapshot_sha256=hashlib.file_digest(snapshot.open('rb'), 'sha256').hexdigest(),
                 interpretation='Vocabulary coverage, NOT prediction accuracy. No Central Park ground truth.')
    save(target / 'species-coverage.json', audit)
    park_path = target / 'central-park.geojson'
    if not park_path.exists():
        response = requests.get(PARKS, params={'$where': "gispropnum='M010'", '$limit': 10}, timeout=60)
        response.raise_for_status()
        records = response.json()
        if len(records) != 1:
            raise ValueError('Expected exactly one Central Park property')
        save(park_path, {'type': 'Feature', 'geometry': records[0]['multipolygon'],
                        'properties': {'name': 'Central Park', 'source': PARKS,
                                       'label_status': 'unavailable', 'prediction_only': True}})
    selection = target / 'imagery-selection.json'
    if not selection.exists():
        items = {}
        for borough, (lon, lat) in CENTERS.items():
            # Pin leaf-on New York 2022 acquisitions; do not mix nearby NJ years.
            bbox = f'{lon-.008},{lat-.008},{lon+.008},{lat+.008}'
            if borough == 'manhattan':
                bbox = '-73.983,40.763,-73.947,40.801'
            response = requests.get(STAC + '/search', params={'collections': 'naip',
                'bbox': bbox, 'datetime': '2022-01-01T00:00:00Z/2022-12-31T23:59:59Z', 'limit': 100}, timeout=60)
            response.raise_for_status()
            found = [f for f in response.json()['features'] if f['id'].startswith('ny_')]
            if not found:
                raise ValueError(f'No 2022 New York coverage for {borough}')
            for feature in found:
                item = items.setdefault(feature['id'], {'item': feature, 'boroughs': []})
                item['boroughs'].append(borough)
        save(selection, {'created_at': datetime.now(timezone.utc).isoformat(),
                         'centers': CENTERS, 'items': items})
    print(json.dumps({'municipal_rows': len(municipal), 'named_row_coverage': audit['named_row_coverage'],
                      'top_species': audit['top_species'], 'unsupported_species': audit['unsupported_species'][:12],
                      'imagery': list(json.loads(selection.read_text())['items'])}, indent=2), flush=True)


def acquire(root):
    from urban_tree_ml.config import ProjectConfig
    from urban_tree_ml.imagery import fetch_stac_item, build_vrt_mosaic
    from urban_tree_ml.resolution_recipe import finer_vrt
    from concurrent.futures import ThreadPoolExecutor

    target = root / 'run-inputs' / RUN
    selection = json.loads((target / 'imagery-selection.json').read_text())
    raw = json.loads((root / 'runs' / PARENT / 'config-usbos.json').read_text())
    raster = root / 'imagery/usnyc/2022/usnyc-2022-cold-start-30cm.vrt'
    raw.update(experiment=RUN, dataset=RUN + '-labeled')
    raw['paths'] = {'root': str(root), 'annotations': str(Path('annotations').resolve())}
    raw['inventory'].update(city='USNYC', parquet_url=str(target / 'municipal-inventory.parquet'))
    raw['split']['projected_crs'] = 'EPSG:32618'
    raw['imagery'].update(boundary_shifts=False, datetime='2022-01-01/2022-12-31',
        source_resolution_m=.6, local_raster=str(raster), item_ids=sorted(selection['items']),
        planting_date_cutoff=min(v['item']['properties']['datetime'][:10] for v in selection['items'].values()))
    raw['reference'] = {
        'taxonomy_path': str(root / 'runs' / PARENT / 'evaluation/validation/taxonomy.json'),
        'normalization_path': str(root / 'chips' / (PARENT + '-ussfo') / 'normalization.json')}
    raw['training']['workers'] = 0
    raw['evaluation']['confidence_threshold'] = .13
    config = ProjectConfig.model_validate(raw)
    save(target / 'config.json', config.model_dump(mode='json'))
    records = []
    for value in selection['items'].values():
        item = value['item']
        records.append({'id': item['id'], 'collection': 'naip', 'datetime': item['properties']['datetime'],
                        'bbox': item['bbox'], 'asset_key': 'image',
                        'asset_href': item['assets']['image']['href'], 'properties': item['properties']})
    index = root / 'imagery/usnyc/stac-items.json'
    if index.exists() and json.loads(index.read_text()).get('items') != records:
        raise ValueError('Existing NYC imagery index differs; refusing to overwrite')
    save(index, {'items': records})
    def fetch(item_id):
        result = fetch_stac_item(config, item_id)
        print(json.dumps({'downloaded': item_id, 'sha256': result.get('sha256')}), flush=True)
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(fetch, sorted(selection['items'])))
    native = config.model_copy(deep=True)
    native.imagery.resolution_m = .6
    native_path = raster.with_name('usnyc-2022-cold-start-native.vrt')
    build_vrt_mosaic(native, '2022', output=native_path)
    if not raster.exists():
        finer_vrt(native_path, raster, .3)
    print(json.dumps({'raster': str(raster), 'config': str(target / 'config.json')}), flush=True)


def build(root):
    import math
    import numpy as np
    import rasterio
    from rasterio.windows import Window, bounds
    from pyproj import Transformer
    from shapely.geometry import shape, box
    from shapely.ops import transform
    from shapely.prepared import prep
    from urban_tree_ml.config import ProjectConfig
    from urban_tree_ml.inventory import export_inventory
    from urban_tree_ml.chips import build_chips
    from urban_tree_ml.prediction_chips import build_prediction_chips
    from urban_tree_ml.splits import Block, split_for_block

    target = root / 'run-inputs' / RUN
    config = ProjectConfig.model_validate(json.loads((target / 'config.json').read_text()))
    source_path = config.imagery.local_raster
    from urban_tree_ml.curation_archive import acquisition
    try:
        acquisition(root, source_path)
    except ValueError as error:
        if str(error) != 'Imagery not registered in curation archive':
            raise
        acquisition(root, source_path, register=True)
    park_ll = shape(json.loads((target / 'central-park.geojson').read_text())['geometry'])
    borough_path = target / 'borough-boundaries.json'
    if not borough_path.exists():
        response = requests.get('https://data.cityofnewyork.us/resource/gthc-hcne.json', timeout=60)
        response.raise_for_status()
        save(borough_path, response.json())
    inventory_path = root / 'inventory/usnyc/inventory.parquet'
    if not inventory_path.exists():
        export_inventory(config)
    frame = pd.read_parquet(inventory_path)
    n = config.imagery.chip_pixels
    with rasterio.open(source_path) as source:
        to_xy = Transformer.from_crs('EPSG:4326', source.crs, always_xy=True)
        park = transform(to_xy.transform, park_ll)
        from shapely import contains_xy
        xs, ys = to_xy.transform(frame.longitude.to_numpy(), frame.latitude.to_numpy())
        # No park inventory claims are used as reference labels or reviewer trees.
        frame = frame.loc[~contains_xy(park, xs, ys)].copy()
        frame.to_parquet(inventory_path, index=False)
        xs, ys = to_xy.transform(frame.longitude.to_numpy(), frame.latitude.to_numpy())
        inv = ~source.transform
        cols, rows = inv.a * xs + inv.c, inv.e * ys + inv.f
        frame['_row'] = (np.floor(rows / n) * n).astype(int)
        frame['_col'] = (np.floor(cols / n) * n).astype(int)
        eligible = frame[frame.split_eligible & frame.split.isin(['train', 'validation'])]
        centers = {name: to_xy.transform(*ll) for name, ll in CENTERS.items()}
        borough_shapes = {v['boroname'].lower().replace(' ','-'): prep(transform(to_xy.transform, shape(v['the_geom'])))
                          for v in json.loads(borough_path.read_text())}
        to_split = Transformer.from_crs(source.crs, config.split.projected_crs, always_xy=True)
        candidates = {}
        for (row, col), group in eligible.groupby(['_row', '_col']):
            if row < 0 or col < 0 or row+n > source.height or col+n > source.width or group.split.nunique() != 1:
                continue
            west, south, east, north = bounds(Window(col, row, n, n), source.transform)
            extent = box(west, south, east, north)
            if park.intersects(extent):
                continue
            corner_x, corner_y = to_split.transform([west,east,east,west], [south,south,north,north])
            blocks = {(math.floor(x/config.split.block_size_m), math.floor(y/config.split.block_size_m))
                      for x,y in zip(corner_x,corner_y)}
            if len(blocks) != 1:
                continue
            bx, by = next(iter(blocks))
            size, guard = config.split.block_size_m, config.split.guard_m
            if any(min(x-bx*size, (bx+1)*size-x, y-by*size, (by+1)*size-y) < guard
                   for x,y in zip(corner_x,corner_y)):
                continue
            split = split_for_block(Block(bx, by), config.split, config.seed)
            if split == 'test' or set(group.split) != {split}:
                continue
            x, y = extent.centroid.coords[0]
            borough = next((name for name, polygon in borough_shapes.items() if polygon.contains(extent)), None)
            if borough is None:
                continue
            key = hashlib.sha256(f'{config.seed}:{borough}:{row}:{col}'.encode()).hexdigest()
            candidates.setdefault((borough, split), []).append((key, int(row), int(col)))
        selected = []
        for borough in CENTERS:
            for split, count in [('train', 40), ('validation', 20)]:
                values = sorted(candidates.get((borough, split), []))[:count]
                if len(values) < 10:
                    raise ValueError(f'Insufficient {borough} {split} coverage: {len(values)}')
                selected.extend(dict(borough=borough, split=split, row=r, col=c) for _,r,c in values)
        selected_keys = {(s['row'],s['col']) for s in selected}
        subset = frame[[key in selected_keys for key in zip(frame._row, frame._col)]].drop(columns=['_row','_col'])
        subset_path = target / 'sample-inventory.parquet'
        subset.to_parquet(subset_path, index=False)
        minx,miny,maxx,maxy = park.bounds
        col0,row1 = inv * (minx,miny)
        col1,row0 = inv * (maxx,maxy)
        windows = [(r,c) for r in range(math.floor(row0/n)*n, math.ceil(row1/n)*n,n)
                   for c in range(math.floor(col0/n)*n, math.ceil(col1/n)*n,n)
                   if park.intersects(box(*bounds(Window(c,r,n,n), source.transform)))]
    save(target / 'sample-selection.json', {'method': 'Fixed-seed hash, 40 train / 20 validation windows per borough; no score selection',
         'labeled_windows': selected, 'central_park_windows': windows,
         'central_park_label_status': 'unavailable', 'test_evaluated': False})
    chip_root = root / 'chips' / config.dataset
    if not (chip_root / 'summary.json').exists():
        print(json.dumps(build_chips(config, source_path, inventory_source=subset_path, use_default_feedback=False)), flush=True)
    unscored = config.model_copy(deep=True)
    unscored.dataset = RUN + '-central-park'
    save(target / 'config-central-park.json', unscored.model_dump(mode='json'))
    if not (root / 'chips' / unscored.dataset / 'summary.json').exists():
        print(json.dumps(build_prediction_chips(unscored, source_path, windows, labels_template=chip_root/'labels.parquet')), flush=True)
    review = root / 'qa/registration' / source_path.stem
    if not (review / 'manifest.json').exists():
        from urban_tree_ml.quality import build_registration_review
        print(json.dumps(build_registration_review(config, source_path, samples=40, window_pixels=n)), flush=True)
    manifest_path = review / 'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    if manifest['metadata'].get('curation_schema_version') != 2:
        if (review / 'reviews.json').exists():
            raise ValueError('Existing NYC curation requires the lossless migration workflow')
        with rasterio.open(source_path) as source:
            crs = source.crs.to_string()
        manifest['metadata'].update(curation_schema_version=2, curation_crs=crs,
            curation_imagery_id=hashlib.sha256(source_path.read_bytes()).hexdigest())
        save(manifest_path, manifest)
    print(json.dumps({'review_workspace_ready': str(review)}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--stage', choices=['prepare', 'acquire', 'build'], default='prepare')
    args = parser.parse_args()
    {'prepare': prepare, 'acquire': acquire, 'build': build}[args.stage](args.root.resolve())
