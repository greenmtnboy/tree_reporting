"""Prepare and score new-year review tiles on CPU with the explicitly chosen parent.

Writes new acquisition-qualified cohorts; never replaces original evaluations or
copies annotations. Unreviewed imagery has no scoring/training supervision.
"""
import argparse
import ctypes
import hashlib
import json
import os
import re
from pathlib import Path


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    Path(path).write_text(json.dumps(value, indent=2)+'\n', encoding='utf-8')


def prepare(root, parent, acquisition):
    import pandas as pd
    import rasterio
    from affine import Affine
    from rasterio.vrt import WarpedVRT
    from rasterio.enums import Resampling
    from rasterio.shutil import copy as raster_copy
    from urban_tree_ml.config import load_config
    from urban_tree_ml.checkpoint_selection import resolve_parent_checkpoint
    from urban_tree_ml.prediction_chips import build_prediction_chips

    records = read(root/'qa/temporal-acquisitions.json')['acquisitions']
    record = next(r for r in records if r['id'] == acquisition and r['state'] == 'ready')
    city = record['city']
    parent_dir = root/'runs'/parent
    checkpoint = resolve_parent_checkpoint(parent_dir)
    directory = root/'run-inputs'/(parent+'-'+acquisition+'-predictions')
    directory.mkdir(parents=True, exist_ok=False)
    config = load_config(parent_dir/f'config-{city}.json')
    config.paths.root = root
    config.dataset = directory.name
    config.experiment = parent
    config.imagery.acquisition_id = acquisition
    config.imagery.provider = 'local'
    config.imagery.datetime = record['date']
    config.imagery.item_ids = [x['item_id'] for x in record['download_receipt']['items']]
    config.reference.taxonomy_path = parent_dir/'vegetation-taxonomy.json'
    original_chips = root/'chips'/(parent+'-'+city)
    config.reference.normalization_path = original_chips/'normalization.json'
    config.training.accelerator = 'cpu'
    config.training.workers = 0
    source = Path(record['raster'])
    model = directory/'imagery-30cm.vrt'
    with rasterio.open(source) as image:
        factor = image.res[0]/config.imagery.resolution_m
        assert factor == 2 and config.imagery.chip_pixels == 512
        with WarpedVRT(image, crs=image.crs, transform=image.transform*Affine.scale(1/factor),
                       width=image.width*2, height=image.height*2,
                       resampling=Resampling.bilinear, nodata=0) as warped:
            raster_copy(warped, model, driver='VRT')
    save(model.with_suffix('.provenance.json'), dict(acquisition_id=acquisition,
         source_sha256=sha(source), model_sha256=sha(model)))
    config.imagery.local_raster = model
    config.imagery.source_resolution_m = .6
    manifest = read(Path(record['review_dir'])/'manifest.json')
    reference = pd.read_parquet(original_chips/'chips.parquet')
    splits = dict(zip(reference.chip_id, reference.split))
    windows = {}
    excluded = []
    for scene in manifest['scenes']:
        chip = scene.get('validation_chip_id')
        if (chip not in splits or splits[chip] not in ('train','validation')
                or not re.fullmatch(r'r\d+_c\d+', chip or '')):
            excluded.append(scene['scene_id']); continue
        row, col = (int(value[1:]) for value in chip.split('_'))
        windows[(row*512,col*512)] = splits[chip]
    if not windows:
        raise ValueError('No unambiguous development chips from the frozen reference manifest')
    save(directory/'plan.json', dict(acquisition_id=acquisition, tiles=len(windows),
         excluded_scenes=excluded, reference_manifest_sha256=sha(original_chips/'chips.parquet')))
    result = build_prediction_chips(config, model, windows, window_splits=windows,
                                    labels_template=original_chips/'labels.parquet')
    save(directory/'config.json', config.model_dump(mode='json'))
    save(directory/'receipt.json', dict(parent=parent, acquisition_id=acquisition,
         checkpoint=str(checkpoint), checkpoint_sha256=sha(checkpoint),
         config_sha256=sha(directory/'config.json'), dataset=result))
    (directory/'READY').write_text('Unlabeled development chips only\n', encoding='utf-8')
    return directory


def predict(directory):
    import torch
    from urban_tree_ml.config import load_config
    from urban_tree_ml.evaluation import run_evaluation
    import pandas as pd
    assert (directory/'READY').is_file()
    receipt = read(directory/'receipt.json')
    assert sha(receipt['checkpoint']) == receipt['checkpoint_sha256']
    assert sha(directory/'config.json') == receipt['config_sha256']
    config = load_config(directory/'config.json')
    manifest = pd.read_parquet(config.paths.root/'chips'/config.dataset/'chips.parquet')
    run = config.paths.root/'runs'/receipt['parent']
    # All destinations are checked before any model work; never retry over results.
    cohorts = {split: f'{split}-{receipt["acquisition_id"]}' for split in sorted(set(manifest.split))}
    for cohort in cohorts.values():
        if (run/'evaluation'/cohort).exists():
            raise ValueError('Temporal prediction cohort already exists; inspect rather than overwrite')
    torch.set_num_threads(2)
    results = []
    for split, cohort in cohorts.items():
        print('Predicting', cohort, int(manifest.split.eq(split).sum()), 'tiles', flush=True)
        result = run_evaluation(config, receipt['checkpoint'], split=split, device_name='cpu',
                                cohort=cohort, output_run=run)
        assert result['prediction_only'] and result['ground_truth_trees'] == 0
        assert result['chips'] == int(manifest.split.eq(split).sum())
        results.append(result)
    save(directory/'prediction-receipt.json', dict(checkpoint_sha256=receipt['checkpoint_sha256'],cohorts=results))
    (directory/'COMPLETE').write_text('Prediction coverage verified\n', encoding='utf-8')


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--parent', required=True)
    parser.add_argument('--acquisitions', nargs='+', required=True)
    args=parser.parse_args()
    if os.name=='nt':
        k=ctypes.windll.kernel32; k.GetCurrentProcess.restype=ctypes.c_void_p
        k.SetProcessAffinityMask.argtypes=[ctypes.c_void_p,ctypes.c_size_t]
        k.SetPriorityClass.argtypes=[ctypes.c_void_p,ctypes.c_ulong]
        assert k.SetProcessAffinityMask(k.GetCurrentProcess(),3)
        assert k.SetPriorityClass(k.GetCurrentProcess(),0x4000)
    root=Path(os.environ['TREE_ML_DATA_ROOT'])
    for acquisition in args.acquisitions:
        directory=prepare(root,args.parent,acquisition)
        predict(directory)
        print(acquisition,'COMPLETE',flush=True)
