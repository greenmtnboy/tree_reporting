"""Prepare bounded development samples from verified NAIP download receipts."""
import argparse
import ctypes
import hashlib
import json
import math
import os
from collections import defaultdict
from datetime import date
from pathlib import Path
import xml.etree.ElementTree as ET


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2)+'\n', encoding='utf-8')


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def limit_cpu():
    if os.name == 'nt':
        kernel = ctypes.windll.kernel32
        kernel.GetCurrentProcess.restype = ctypes.c_void_p
        kernel.SetProcessAffinityMask.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
        kernel.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        assert kernel.SetProcessAffinityMask(kernel.GetCurrentProcess(), 3)
        assert kernel.SetPriorityClass(kernel.GetCurrentProcess(), 0x4000)


def prepare_review_inventory(config, chips, work):
    """Seed only sampled development windows from the available city inventory."""
    import numpy as np
    import pandas as pd
    import rasterio
    from pyproj import Transformer
    from urban_tree_ml.inventory import _add_source_label_eligibility, planted_after_imagery
    from urban_tree_ml.splits import assign_spatial_splits
    from urban_tree_ml.taxonomy import Taxonomy, encode_taxonomy

    city = config.inventory.city.lower()
    source = (Path(config.inventory.parquet_url) if city == 'usnyc'
              else config.paths.root/'inventory'/city/'inventory.parquet')
    inventory = pd.read_parquet(source)
    with rasterio.open(config.imagery.local_raster) as raster:
        project = Transformer.from_crs('EPSG:4326', raster.crs, always_xy=True)
        xs, ys = project.transform(inventory.longitude.to_numpy(), inventory.latitude.to_numpy())
        columns, rows = (~raster.transform)*(xs, ys)
    size = config.imagery.chip_pixels
    chip_ids = pd.Series([f'r{int(y//size):06d}_c{int(x//size):06d}'
                         for x, y in zip(columns, rows)], index=inventory.index)
    inventory = inventory[chip_ids.isin(set(chips.chip_id))].copy()
    inventory = inventory[~planted_after_imagery(inventory, config.imagery.planting_date_cutoff)].copy()
    inventory = assign_spatial_splits(inventory, config.split, config.seed)
    inventory['split'] = inventory['split'].astype('string')
    inventory = inventory[inventory.split.isin(['train', 'validation']) & inventory.split_eligible].copy()
    inventory = _add_source_label_eligibility(inventory, config.inventory)
    taxonomy = Taxonomy(**read(config.reference.taxonomy_path))
    inventory = encode_taxonomy(inventory, taxonomy)
    inventory['genus_eligible'] = inventory.taxon_eligible & inventory.genus_id.ge(0)
    inventory['species_eligible'] = inventory.taxon_eligible & inventory.species_id.ge(0)
    inventory['dbh_log1p'] = np.log1p(inventory.diameter_at_breast_height.where(inventory.dbh_eligible))
    # Pixel columns in a previous preparation belong to that raster's grid.
    inventory = inventory.drop(columns=['pixel_col', 'pixel_row'], errors='ignore')
    destination = work/'review-inventory.parquet'
    inventory.to_parquet(destination, index=False)
    save(work/'review-inventory-source.json', dict(source=str(source), source_sha256=sha(source),
         sampled_development_rows=len(inventory), output_sha256=sha(destination), test_evaluated=False))
    return destination, inventory


def prepare(root, parent, receipt_path, *, template_city=None, city_label=None, boundary_file=None, review_tag='20261116'):
    import numpy as np
    import pandas as pd
    import rasterio
    import torch
    from affine import Affine
    from pyproj import Transformer
    from rasterio.enums import Resampling
    from rasterio.shutil import copy as raster_copy
    from rasterio.vrt import WarpedVRT
    from rasterio.windows import Window
    from shapely.geometry import shape, box
    from shapely.ops import transform, unary_union
    from urban_tree_ml.config import load_config
    from urban_tree_ml.checkpoint_selection import resolve_parent_checkpoint
    from urban_tree_ml.evaluation import run_evaluation
    from urban_tree_ml.prediction_chips import build_prediction_chips
    from urban_tree_ml.quality import append_validation_chip_to_registration_review, render_registration_review_html
    from urban_tree_ml.feedback import persist_review_payload
    from urban_tree_ml.splits import Block, split_for_block

    torch.set_num_threads(2)
    receipt = read(receipt_path)
    assert receipt['status'] == 'downloaded_verified_not_registered'
    grouped = defaultdict(list)
    for asset in receipt['assets']:
        grouped[(asset['city'], asset['year'])].append(asset)
    catalog = read(Path(receipt_path).parent/'selection.json')
    footprints = {entry['item']['id']: shape(entry['item']['geometry']) for entry in catalog['selected']}
    model = root/'runs'/parent
    checkpoint = resolve_parent_checkpoint(model)
    checkpoint_sha = sha(checkpoint)
    records = []
    for (city, year), assets in sorted(grouped.items()):
        key = f'{city}-naip-{year}'
        work = root/'run-inputs'/f'{key}-review-{review_tag}'
        if (work/'READY').is_file():
            ready = read(work/'ready-receipt.json')
            assert ready['checkpoint_sha256'] == checkpoint_sha
            records.append(ready)
            continue
        assert not work.exists(), f'Inspect partial preparation before resuming {work}'
        assert not any(r['id'] == key for r in read(root/'qa/temporal-acquisitions.json')['acquisitions'])
        work.mkdir(parents=True)
        print('Preparing', key, len(assets), 'new source images', flush=True)
        template = template_city or city
        config = load_config(model/f'config-{template}.json')
        if template != city:
            config.inventory.city = city.upper()
            config.inventory.parquet_url = str(root/'inventory'/city/'published-v2.parquet')
        config.paths.root = root
        config.paths.annotations = Path(__file__).resolve().parents[1]/'annotations'
        config.experiment = parent
        config.dataset = f'{parent}-{key}-review-{review_tag}'
        config.imagery.acquisition_id = key
        config.imagery.boundary_shifts = False
        config.imagery.item_ids = [a['item_id'] for a in assets]
        config.imagery.datetime = f'{year}-01-01/{year}-12-31'
        config.imagery.planting_date_cutoff = max(date.fromisoformat(a['datetime'][:10]) for a in assets)
        config.training.accelerator = 'cpu'
        config.training.workers = 0
        config.training.batch_size = 2
        config.evaluation.confidence_threshold = .13
        config.reference.taxonomy_path = model/'vegetation-taxonomy.json'
        config.reference.normalization_path = root/'chips'/(parent+'-'+template)/'normalization.json'
        config.targets.background_mode = 'curated_only'
        projection = Transformer.from_crs('EPSG:4326', config.split.projected_crs, always_xy=True).transform
        boundary_path = (root/'run-inputs/fairfax-source-v2/boundary.geojson' if city == 'usffx'
                         else Path(receipt_path).parent/'nyc-boundary.geojson')
        boundary_path = boundary_file or boundary_path
        boundary_json = read(boundary_path)
        boundary = unary_union([shape(f['geometry']) for f in boundary_json['features']])
        boundary = transform(projection, boundary)
        photos = {a['item_id']: transform(projection, footprints[a['item_id']]) for a in assets}
        bounds = unary_union(list(photos.values())).bounds
        tile_m = 153.6
        left = math.floor(bounds[0]/tile_m)*tile_m
        top = math.ceil(bounds[3]/tile_m)*tile_m
        width = math.ceil((bounds[2]-left)/tile_m)*512
        height = math.ceil((top-bounds[1])/tile_m)*512
        affine = Affine(.3, 0, left, 0, -.3, top)
        raster_path = work/'imagery-30cm.vrt'
        document = ET.Element('VRTDataset', rasterXSize=str(width), rasterYSize=str(height))
        ET.SubElement(document, 'SRS').text = config.split.projected_crs
        ET.SubElement(document, 'GeoTransform').text = ','.join(map(str, affine.to_gdal()))
        for asset in assets:
            path = Path(asset['path'])
            assert path.stat().st_size == asset['bytes']
            assert read(path.with_suffix('.manifest.json'))['sha256'] == asset['sha256']
            warped = work/(asset['item_id']+'.vrt')
            with rasterio.open(path) as source:
                with WarpedVRT(source, crs=config.split.projected_crs, transform=affine,
                               width=width, height=height, resampling=Resampling.bilinear, nodata=0) as view:
                    raster_copy(view, warped, driver='VRT')
        for band, color in enumerate(('Red','Green','Blue','Undefined'), 1):
            output = ET.SubElement(document, 'VRTRasterBand', dataType='Byte', band=str(band))
            ET.SubElement(output, 'ColorInterp').text = color
            ET.SubElement(output, 'NoDataValue').text = '0'
            for asset in assets:
                entry = ET.SubElement(output, 'ComplexSource')
                ET.SubElement(entry, 'SourceFilename', relativeToVRT='1').text = asset['item_id']+'.vrt'
                ET.SubElement(entry, 'SourceBand').text = str(band)
                for rect in ('SrcRect','DstRect'):
                    ET.SubElement(entry, rect, xOff='0', yOff='0', xSize=str(width), ySize=str(height))
                ET.SubElement(entry, 'NODATA').text = '0'
        ET.ElementTree(document).write(raster_path, encoding='utf-8', xml_declaration=True)
        review_raster = work/'imagery.vrt'
        windows = {}
        sampled = []
        with rasterio.open(raster_path) as source:
            with WarpedVRT(source, crs=source.crs, transform=affine*Affine.scale(2),
                           width=width//2, height=height//2, resampling=Resampling.bilinear, nodata=0) as view:
                raster_copy(view, review_raster, driver='VRT')
            for asset in assets:
                area = photos[asset['item_id']].intersection(boundary)
                if area.is_empty:
                    continue
                candidates = []
                x0,y0,x1,y1 = area.bounds
                for row in range(max(0,math.floor((top-y1)/tile_m)), min(height//512,math.ceil((top-y0)/tile_m))):
                    for col in range(max(0,math.floor((x0-left)/tile_m)), min(width//512,math.ceil((x1-left)/tile_m))):
                        x,y = affine*(col*512,row*512)
                        if not area.covers(box(x,y-tile_m,x+tile_m,y)):
                            continue
                        block, guard = config.split.block_size_m, config.split.guard_m
                        splits = {split_for_block(Block(bx,by),config.split,config.seed)
                                  for bx in range(math.floor((x-guard)/block),math.floor((x+tile_m+guard)/block)+1)
                                  for by in range(math.floor((y-tile_m-guard)/block),math.floor((y+guard)/block)+1)}
                        if len(splits) == 1 and next(iter(splits)) in {'train','validation'}:
                            split = next(iter(splits))
                            rank = hashlib.sha256(f'{config.seed}:{asset["item_id"]}:{row}:{col}'.encode()).hexdigest()
                            candidates.append((rank,row,col,split))
                counts = {'train':0,'validation':0}
                for _,row,col,split in sorted(candidates):
                    if counts[split] >= {'train':10,'validation':5}[split] or (row*512,col*512) in windows:
                        continue
                    valid = np.all(source.read_masks(window=Window(col*512,row*512,512,512))>0,axis=0).mean()
                    if valid < .99:
                        continue
                    # Some NAIP collars are flagged as valid, with all RGBN samples white.
                    rgbn = source.read(window=Window(col*512,row*512,512,512))
                    if np.all(rgbn == 255) or np.all(rgbn == 0):
                        continue
                    windows[(row*512,col*512)] = split
                    sampled.append(dict(item_id=asset['item_id'],row=row*512,col=col*512,split=split,valid_fraction=float(valid)))
                    counts[split] += 1
                    if counts == {'train':10,'validation':5}:
                        break
                print(asset['item_id'], counts, flush=True)
        assert windows and set(windows.values()) == {'train','validation'}
        save(work/'sample-plan.json', dict(windows=sampled, boundary_sha256=sha(boundary_path), test_evaluated=False))
        config.imagery.local_raster = raster_path
        config.imagery.source_resolution_m = assets[0]['resolution_m']
        save(raster_path.with_suffix('.provenance.json'),dict(acquisition_id=key,
             source_sha256=sha(review_raster), model_sha256=sha(raster_path)))
        save(work/'prediction-config.json',config.model_dump(mode='json'))
        build_prediction_chips(config,raster_path,windows,window_splits=windows,
            labels_template=root/'chips'/(parent+'-'+template)/'labels.parquet')
        chips_path = root/'chips'/config.dataset/'chips.parquet'
        chips = pd.read_parquet(chips_path)
        inventory_path, inventory = prepare_review_inventory(config, chips, work)
        review_config = config.model_copy(deep=True)
        review_config.imagery.local_raster = review_raster
        review_config.imagery.resolution_m = .6
        review_config.imagery.chip_pixels = 256
        save(work/'config.json',review_config.model_dump(mode='json'))
        review = root/'qa/registration'/key
        assert not review.exists()
        (review/'images').mkdir(parents=True)
        split_policy = dict(seed=config.seed,splits=config.split.model_dump(mode='json'),reference_grid_sha256=sha(raster_path))
        split_sha = hashlib.sha256(json.dumps(split_policy,sort_keys=True).encode()).hexdigest()
        manifest = dict(metadata=dict(review_id=key,curation_schema_version=2,city=city,
            source_raster=str(review_raster),inventory=str(inventory_path),
            window_pixels=256,included_splits=['train','validation'],test_labels_included=False,
            acquisition_id=key,curation_imagery_id=sha(review_raster),curation_crs=config.split.projected_crs,
            split_policy_sha256=split_sha,reference_split_manifest_sha256=sha(chips_path)), samples=[],scenes=[])
        for chip in chips.itertuples():
            append_validation_chip_to_registration_review(review_config,review_raster,review,chip.chip_id,
                inventory.iloc[:0],_manifest=manifest,_persist=False,_inventory=inventory)
            manifest['scenes'][-1]['splits'] = [chip.split]
        save(review/'manifest.json',manifest)
        persist_review_payload(review,dict(reviews={},scene_reviews={},mask_regions=[]))
        (review/'index.html').write_text(render_registration_review_html(review),encoding='utf-8')
        record = dict(id=key,city=city,label=f'{city_label or ("Fairfax" if city=="usffx" else "New York")} · {year}',
            date=max(a['datetime'][:10] for a in assets),state='ready',config_path=str(work/'config.json'),
            raster=str(review_raster),review_dir=str(review),split_policy_sha256=split_sha,
            reference_grid=str(raster_path),reference_chips=str(chips_path),scenes=len(chips),
            source_dates=sorted({a['datetime'] for a in assets}),download_receipt=dict(items=assets),
            training_admission='published human curation only',validation_admission='full-tile more_done only')
        save(work/'acquisition.json',record)
        cohorts = []
        for split in ('train','validation'):
            cohort = split+'-'+key
            assert not (model/'evaluation'/cohort).exists()
            print('Inferring',cohort,int(chips.split.eq(split).sum()),flush=True)
            run_evaluation(config,checkpoint,split=split,device_name='cpu',cohort=cohort,output_run=work/'staging')
            coverage = read(work/'staging/evaluation'/cohort/'inference-coverage.json')
            assert coverage['complete'] and {x['chip_id'] for x in coverage['chips']} == set(chips.loc[chips.split.eq(split),'chip_id'])
            cohorts.append(cohort)
        ready = dict(acquisition=record,cohorts=cohorts,checkpoint_sha256=checkpoint_sha,work=str(work),
            chips=len(chips),splits=chips.groupby('split').size().to_dict(),
            outputs_sha256={str(p):sha(p) for p in (work/'staging').rglob('*') if p.is_file()})
        save(work/'ready-receipt.json',ready)
        (work/'READY').write_text('Prepared scenes and predictions verified; registration pending.\n')
        records.append(ready)
        print('READY',key,len(chips),'tiles',flush=True)
    save(Path(receipt_path).parent/'prepared-acquisitions.json',dict(parent=parent,acquisitions=records))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--receipt',type=Path,required=True)
    parser.add_argument('--parent',required=True)
    parser.add_argument('--template-city', help='Reuse a trained city model config for a new review city')
    parser.add_argument('--city-label')
    parser.add_argument('--boundary-file', type=Path, help='Review jurisdiction in WGS84 GeoJSON')
    parser.add_argument('--review-tag', default='20261116')
    args = parser.parse_args()
    if args.template_city and not (args.city_label and args.boundary_file):
        parser.error('--template-city requires the review city label and boundary')
    limit_cpu()
    prepare(Path(os.environ['TREE_ML_DATA_ROOT']),args.parent,args.receipt,template_city=args.template_city,
            city_label=args.city_label,boundary_file=args.boundary_file,review_tag=args.review_tag)
