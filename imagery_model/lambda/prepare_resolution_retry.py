"""Retry a failed 30 cm preparation using immutable inputs and real-chip smoke checks."""
import argparse
from datetime import date
import hashlib
import json
import shutil
import tarfile
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
from pyproj import Transformer
import rasterio

from urban_tree_ml.chips import build_chips
from urban_tree_ml.config import load_config, ReferenceConfig, TargetsConfig
from urban_tree_ml.feedback import load_training_feedback
from urban_tree_ml.freeze_next_run import validate_frozen_city
from urban_tree_ml.resolution_recipe import apply_resolution_recipe


def prepare(root, source, experiment):
    for name in (source, experiment):
        if not name or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-' for c in name):
            raise ValueError('Invalid experiment name')
    original = root/'run-inputs'/source
    destination = root/'run-inputs'/experiment
    if (root/f'{experiment}-instance.json').exists() or (destination/'resolution-preflight.json').exists():
        raise ValueError('Retry already prepared or allocated')
    if json.loads((original/'resolution-policy.json').read_text()) != {'recipe': 'swin-native30-upsample60-v1'}:
        raise ValueError('Expected fresh 30 cm recipe')
    if (original/'warm-start.json').exists():
        raise ValueError('Must remain a fresh ImageNet run')
    for city in ('ussfo', 'usbos'):
        validate_frozen_city(original/city)
    if not destination.exists():
        shutil.copytree(original, destination)
    hashes = {}
    for path in original.rglob('*'):
        if path.is_file():
            relative = path.relative_to(original)
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            assert hashlib.sha256((destination/relative).read_bytes()).hexdigest() == digest
            hashes[relative.as_posix()] = digest
    results = {}
    work = Path(__file__).resolve().parents[1]
    for city, suffix in [('ussfo', 'sf'), ('usbos', 'boston')]:
        config = load_config(work/f'configs/sf_boston_vocab_v2_{suffix}.yaml')
        config.paths.root = root
        original_raster = config.imagery.local_raster
        config.targets = TargetsConfig.model_validate({**config.targets.model_dump(),
            **json.loads((destination/'center-policy.json').read_text())})
        dates = json.loads((destination/'imagery-dates.json').read_text())
        config.imagery.planting_date_cutoff = date.fromisoformat(dates[city]['cutoff'])
        apply_resolution_recipe(config, experiment, reuse_verified=True)
        feedback = load_training_feedback(destination/city/'training-feedback.json',
            config.imagery.local_raster, config.dataset, source_raster=original_raster)
        frame = pd.read_parquet(destination/city/'inventory.parquet')
        frame = frame[frame.split_eligible & frame.split.isin(['train', 'validation'])].copy()
        with rasterio.open(config.imagery.local_raster) as raster:
            transform = raster.transform
            crs = raster.crs
            xs, ys = Transformer.from_crs('EPSG:4326', crs, always_xy=True).transform(
                frame.longitude.to_numpy(), frame.latitude.to_numpy())
            frame['_col'] = np.floor((np.asarray(xs)-transform.c)/(.3*512)).astype(int)
            frame['_row'] = np.floor((transform.f-np.asarray(ys))/(.3*512)).astype(int)
        keys = []
        catalog = pd.read_parquet(root/'chips'/f'sf-boston-naip-swin-v9-finetune-{city}'/'chips.parquet')
        for split in ('train', 'validation'):
            counts = frame[frame.split == split].groupby(['_row','_col']).size()
            choices = counts[(counts >= 10) & (counts <= 60)]
            valid = {(int(r.row_offset)//256, int(r.column_offset)//256)
                     for r in catalog[catalog.split == split].itertuples()}
            choices = choices[[key in valid for key in choices.index]]
            if choices.empty:
                raise ValueError(f'No modest {city}/{split} smoke chip')
            keys.append(choices.index[0])
        sample = frame[[tuple(v) in keys for v in frame[['_row','_col']].to_numpy()]]
        # Materialize only two real chips; never inspect sealed test examples.
        # Keep geographic masks; limit independent added centers to those chips.
        project = Transformer.from_crs('EPSG:4326', crs, always_xy=True)
        def in_sample(region):
            if region['mode'] != 'confirmed-tree' or region.get('tree_id'):
                return True
            x,y = project.transform(region['anchor_longitude'], region['anchor_latitude'])
            col = int(np.floor((x+region['east_m']-transform.c)/153.6))
            row = int(np.floor((transform.f-y-region['north_m'])/153.6))
            return (row,col) in keys
        feedback['region_overrides'] = [r for r in feedback.get('region_overrides',[]) if in_sample(r)]
        config.reference = ReferenceConfig(taxonomy_path=destination/'ussfo/taxonomy.json',
            normalization_path=root/'chips/sf-boston-naip-swin-v9-finetune-ussfo/normalization.json')
        with tempfile.TemporaryDirectory(prefix='tree-resolution-smoke-') as temporary:
            temporary = Path(temporary)
            config.paths.root = temporary
            sample.drop(columns=['_col','_row']).to_parquet(temporary/'inventory.parquet', index=False)
            (temporary/'feedback.json').write_text(json.dumps(feedback))
            summary = build_chips(config, config.imagery.local_raster,
                feedback_path=temporary/'feedback.json', feedback_source_raster=original_raster,
                inventory_source=temporary/'inventory.parquet')
            manifest = pd.read_parquet(summary['manifest'])
            if not {'train','validation'} <= set(manifest.split):
                raise ValueError(f'{city} smoke missing development split')
            for chip in manifest.itertuples():
                with np.load(Path(summary['manifest']).parent/chip.path) as values:
                    assert values['image'].shape == (4,512,512)
                    assert np.isfinite(values['image']).all()
            results[city] = {k: summary[k] for k in ('chips','trees','feedback_excluded_points',
                'feedback_point_corrected_points','feedback_mask_regions')}
            print(city, json.dumps(results[city]), flush=True)
    receipt = {'source_run':source, 'frozen_file_sha256':hashes, 'real_chip_smoke':results}
    (destination/'resolution-preflight.json').write_text(json.dumps(receipt, indent=2))
    with tarfile.open(root/f'{experiment}-inputs.tar.gz', 'w:gz') as archive:
        archive.add(destination, arcname=experiment)
    print('Verified frozen-input retry ready:', experiment, flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--source', required=True)
    parser.add_argument('--experiment', required=True)
    args = parser.parse_args()
    prepare(args.root, args.source, args.experiment)
