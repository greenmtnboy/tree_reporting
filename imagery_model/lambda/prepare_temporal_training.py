"""Freeze published acquisition curation and portable imagery for a joint fine-tune."""
import json
import os
import re
import shutil
import tarfile
import tempfile
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

import pandas as pd
import rasterio

from urban_tree_ml.config import load_config
from urban_tree_ml.curation_admission import enable, frozen_context
from urban_tree_ml.freeze_next_run import freeze
from urban_tree_ml.prepared_finetune import sha
from urban_tree_ml.temporal_training import admitted_splits


def save(path, value):
    path.write_text(json.dumps(value, indent=2)+'\n', encoding='utf-8')


def portable_raster(source, directory, root, assets, copied):
    """Copy VRT graphs into this freeze, preserving every pixel operation."""
    source = Path(source).resolve()
    if not source.is_relative_to(root.resolve()):
        raise ValueError('Temporal imagery dependency is outside artifact root')
    if source.suffix.lower() != '.vrt':
        assets[source.relative_to(root).as_posix()] = sha(source)
        return source
    if source in copied:
        return copied[source]
    destination = directory/source.name
    if destination.exists():
        raise ValueError('Temporal VRT dependency names collide')
    copied[source] = destination
    tree = ET.parse(source)
    for node in tree.findall('.//SourceFilename')+tree.findall('.//SourceDataset'):
        path = Path(node.text)
        path = (source.parent/path).resolve() if node.get('relativeToVRT') == '1' else path.resolve()
        target = portable_raster(path, directory, root, assets, copied)
        node.text = os.path.relpath(target, destination.parent).replace('\\', '/')
        node.set('relativeToVRT', '1')
    tree.write(destination, encoding='utf-8', xml_declaration=True)
    return destination


def prepare(root, inputs, parent, acquisitions):
    records = json.loads((root/'qa/temporal-acquisitions.json').read_text())['acquisitions']
    configs, assets, audits = {}, {}, {}
    for key in acquisitions:
        if not re.fullmatch(r'(usbos|ussfo|usffx|usnyc|uswas)-naip-\d{4}', key):
            raise ValueError('Invalid temporal training acquisition')
        record = next(item for item in records if item['id'] == key and item['state'] == 'ready')
        city = record['city']
        review = Path(record['review_dir'])
        original_config = load_config(record['config_path'])
        if (original_config.inventory.city.lower() != city or
                original_config.imagery.acquisition_id != key or not key.startswith(city+'-')):
            raise ValueError('Acquisition registry and config identify different imagery')
        manifest = json.loads((review/'manifest.json').read_text())
        # A new city has no training-parent city config yet. Its verified review
        # prediction used the parent's weights, vocabulary and normalization.
        new_city = not (root/'runs'/parent/f'config-{city}.json').is_file()
        if new_city:
            work = Path(record['config_path']).parent
            config = load_config(work/'prediction-config.json')
            if (config.experiment != parent or config.inventory.city.lower() != city
                    or config.imagery.acquisition_id != key
                    or config.imagery.local_raster.resolve() != Path(record['reference_grid']).resolve()):
                raise ValueError('New-city prediction config does not match parent and acquisition')
            inventory = work/'review-inventory.parquet'
            inventory_receipt = json.loads((work/'review-inventory-source.json').read_text())
            if (Path(manifest['metadata']['inventory']).resolve() != inventory.resolve()
                    or sha(inventory) != inventory_receipt['output_sha256']
                    or inventory_receipt['test_evaluated'] is not False):
                raise ValueError('New-city inventory differs from the sampled development inventory')
            taxonomy = inputs/'vegetation-taxonomy.json'
            if sha(config.reference.taxonomy_path) != sha(taxonomy):
                if json.loads(config.reference.taxonomy_path.read_text()) != json.loads(taxonomy.read_text()):
                    raise ValueError('New-city review taxonomy differs from frozen training vocabulary')
        else:
            config = load_config(root/'runs'/parent/f'config-{city}.json')
            inventory = inputs/city/'inventory.parquet'
            taxonomy = inputs/city/'taxonomy.json'
        reference_path = Path(record.get('reference_chips') or root/'chips'/(parent+'-'+city)/'chips.parquet').resolve()
        if not reference_path.is_relative_to((root/'chips').resolve()):
            raise ValueError('Acquisition reference chips must belong to the artifact catalog')
        if sha(reference_path) != manifest['metadata']['reference_split_manifest_sha256']:
            raise ValueError('Temporal reference split manifest changed')
        reference = pd.read_parquet(reference_path)
        reference = reference[reference.chip_id.str.fullmatch(r'r\d{6}_c\d{6}')]
        if not set(reference.split) <= {'train', 'validation'}:
            raise ValueError('Temporal reference includes sealed tests')
        reference_raster = Path(record['reference_grid'])
        split_policy = dict(seed=original_config.seed, splits=original_config.split.model_dump(mode='json'),
                            reference_grid_sha256=sha(reference_raster))
        import hashlib
        if hashlib.sha256(json.dumps(split_policy, sort_keys=True).encode()).hexdigest() != record['split_policy_sha256']:
            raise ValueError('Temporal geographic split policy changed')
        with tempfile.TemporaryDirectory(prefix='tree-temporal-freeze-') as temp:
            staging = Path(temp)
            prepared = staging/'prepared'
            (prepared/city).mkdir(parents=True)
            shutil.copy2(inventory, prepared/city/'inventory.parquet')
            shutil.copy2(taxonomy, prepared/city/'taxonomy.json')
            save(prepared/'preparation.json', {'acquisition_id': key, 'test_evaluated': False})
            freeze(prepared, {city: review}, staging/'frozen')
            shutil.copytree(staging/'frozen'/city, inputs/key)
        directory = inputs/key
        enable(directory)
        reference[['chip_id','split']].to_parquet(directory/'reference-chips.parquet', index=False)
        copied = {}
        raster = portable_raster(record['raster'], directory, root, assets, copied)
        model_raster = directory/'training-grid-30cm.vrt'
        if model_raster.exists():
            raise ValueError('Training raster would overwrite a frozen source dependency')
        from affine import Affine
        from rasterio.vrt import WarpedVRT
        from rasterio.enums import Resampling
        from rasterio.shutil import copy as raster_copy
        with rasterio.open(raster) as source:
            if source.res != (.6, .6):
                raise ValueError('Temporal review grid must be 60 cm')
            if new_city:
                # The review VRT itself was downsampled from this exact native mosaic.
                # Reuse the frozen upstream dependency instead of enlarging that preview.
                model_raster = copied[config.imagery.local_raster.resolve()]
                source_node = ET.parse(raster).find('GDALWarpOptions/SourceDataset')
                if (source_node is None or source_node.get('relativeToVRT') != '1'
                        or (raster.parent/source_node.text).resolve() != model_raster.resolve()):
                    raise ValueError('Review imagery is not derived directly from the prediction mosaic')
            else:
                with WarpedVRT(source, crs=source.crs, transform=source.transform*Affine.scale(.5),
                               width=source.width*2, height=source.height*2,
                               resampling=Resampling.bilinear, nodata=0) as warped:
                    raster_copy(warped, model_raster, driver='VRT')
                tree = ET.parse(model_raster)
                node = tree.find('.//SourceDataset')
                node.text = raster.name
                node.set('relativeToVRT', '1')
                tree.write(model_raster, encoding='utf-8', xml_declaration=True)
        config.paths.root = root
        config.experiment = inputs.name
        config.dataset = json.loads((directory/'training-feedback.json').read_text())['dataset']
        config.imagery.acquisition_id = key
        config.imagery.local_raster = model_raster
        config.imagery.boundary_shifts = False
        config.imagery.source_resolution_m = original_config.imagery.source_resolution_m
        config.imagery.item_ids = original_config.imagery.item_ids
        config.imagery.datetime = original_config.imagery.datetime
        config.imagery.planting_date_cutoff = max(date.fromisoformat(item[:10]) for item in record['source_dates'])
        config.targets.background_mode = 'curated_only'
        config.reference.taxonomy_path = inputs/'vegetation-taxonomy.json'
        if not new_city:
            config.reference.normalization_path = root/'chips'/(parent+'-'+city)/'normalization.json'
        if config.split != original_config.split or config.seed != original_config.seed:
            raise ValueError('Parent and temporal geographic split policy differ')
        manifest, state, feedback = frozen_context(directory)
        with rasterio.open(model_raster) as source:
            candidates = admitted_splits(manifest, state, feedback, source, 512, reference)
        from rasterio.windows import Window
        import numpy as np
        excluded = []
        with rasterio.open(model_raster) as source:
            for chip in candidates:
                row, col = int(chip[1:7])*512, int(chip.split('_c')[1])*512
                if (row+512 > source.height or col+512 > source.width or
                        np.all(source.read_masks(config.imagery.bands, window=Window(col,row,512,512)) > 0,
                               axis=0).mean() < config.imagery.minimum_valid_fraction):
                    excluded.append(chip)
        excluded.sort()
        admitted = {chip: split for chip, split in candidates.items() if chip not in excluded}
        if not admitted:
            raise ValueError('Acquisition has no covered published curation: '+key)
        files = [*copied.values(), model_raster, directory/'reference-chips.parquet',
                 config.reference.normalization_path]
        checksums = {path.relative_to(root).as_posix(): sha(path) for path in files}
        source_assets = {Path(item['path']).resolve(): item['sha256'] for item in record['download_receipt']['items']}
        for path, expected in source_assets.items():
            if assets.get(path.relative_to(root).as_posix()) != expected:
                raise ValueError('Temporal source pixels differ from verified download')
            checksums[path.relative_to(root).as_posix()] = expected
        policy = dict(schema_version=1, acquisition_id=key, city=city, test_evaluated=False,
                      model_raster=model_raster.relative_to(root).as_posix(),
                      source_raster=raster.relative_to(root).as_posix(), seed=config.seed,
                      split=config.split.model_dump(mode='json'), checksums=checksums,
                      candidates=candidates, excluded_nodata=excluded, admitted=admitted)
        save(directory/'temporal-admission.json', policy)
        raw = config.model_dump(mode='json')
        raw['imagery']['local_raster'] = policy['model_raster']
        for field in ('taxonomy_path', 'normalization_path'):
            raw['reference'][field] = Path(raw['reference'][field]).relative_to(root).as_posix()
        configs[key] = raw
        audits[key] = dict(train=sum(v == 'train' for v in admitted.values()),
                           validation=sum(v == 'validation' for v in admitted.values()), excluded_nodata=excluded)
        print(key, json.dumps(audits[key]), flush=True)
    # Extend the newly prepared run's asset bundle once, retaining Fairfax assets.
    receipt_path = root/(inputs.name+'-assets.json')
    existing = json.loads(receipt_path.read_text()) if receipt_path.exists() else {}
    if set(existing) & set(assets):
        raise ValueError('Temporal and existing asset destinations overlap')
    all_assets = {**existing, **assets}
    archive_path = root/(inputs.name+'-assets.tar')
    temporary = archive_path.with_suffix('.temporal-part')
    with tarfile.open(temporary, 'w') as archive:
        for relative, expected in all_assets.items():
            archive.add(root/relative, arcname=relative, recursive=False)
    temporary.replace(archive_path)
    save(receipt_path, all_assets)
    save(inputs/'temporal-joint.json', dict(experiment=inputs.name, configs=configs,
                                          test_evaluated=False, admission_counts=audits))
    return audits
