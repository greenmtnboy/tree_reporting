"""Fresh train-only geographic ablation, preserving v5 pixels and evaluation labels."""
import argparse
import hashlib
import json
import os
import shutil
import tarfile
import urllib.request
from pathlib import Path

import pandas as pd

SOURCE = 'sf-boston-naip-curation-v5-retrain'
BOUNDARY = ('https://gis.arboretum.harvard.edu/arcgis/rest/services/Maps/Explorer/MapServer/6/'
            'query?where=1%3D1&outFields=*&outSR=4326&f=geojson')


def filter_training(old, city, ids):
    remove = ((old.split=='train') & old.chip_id.isin(ids)) if city=='usbos' else pd.Series(False,index=old.index)
    new = old[~remove].copy()
    pd.testing.assert_frame_equal(old[old.split!='train'],new[new.split!='train'])
    return new


def prepare(root, experiment):
    import rasterio
    from pyproj import Transformer
    from shapely.geometry import Polygon, shape
    from shapely.ops import transform, unary_union

    parent = root / 'runs' / SOURCE
    assert (parent / 'COMPLETE').exists()
    payload = urllib.request.urlopen(BOUNDARY, timeout=60).read()
    boundary = json.loads(payload)
    assert boundary['features'] and not boundary.get('exceededTransferLimit')
    polygons = unary_union([shape(f['geometry']) for f in boundary['features']])
    config = json.loads((parent / 'config-usbos.json').read_text())
    raster = root / 'imagery/usbos/2023/usbos-2023-external.vrt'
    chips = pd.read_parquet(root / 'chips' / config['dataset'] / 'chips.parquet')
    inventory = pd.read_parquet(root / 'run-inputs' / SOURCE / 'usbos/inventory.parquet')
    arboretum = inventory[inventory.data_source == 'ARNOLD_ARBORETUM']
    assert len(arboretum) > 100
    size = config['imagery']['chip_pixels']
    with rasterio.open(raster) as image:
        projector = Transformer.from_crs(4326, image.crs, always_xy=True)
        region = transform(projector.transform, polygons)
        assert region.is_valid and not region.is_empty
        x,y = projector.transform(arboretum.longitude.to_numpy(), arboretum.latitude.to_numpy())
        col,row = (~image.transform) * (x,y)
        source_chips = {f'r{int(y//size):06d}_c{int(x//size):06d}' for x,y in zip(col,row)}
        excluded = []
        for c in chips[chips.split == 'train'].itertuples():
            x,y = c.column_offset,c.row_offset
            footprint = Polygon([image.transform * p for p in
                                 [(x,y),(x+size,y),(x+size,y+size),(x,y+size)]])
            intersects = footprint.intersects(region)
            if intersects or c.chip_id in source_chips:
                excluded.append({'chip_id':c.chip_id,'boundary_intersection':intersects,
                                 'source_membership':c.chip_id in source_chips})
    assert excluded and len(excluded) < len(chips[chips.split == 'train'])
    labels = pd.read_parquet(root / 'chips' / config['dataset'] / 'labels.parquet')
    ids = {r['chip_id'] for r in excluded}
    manifest = {'source_run': SOURCE, 'policy': 'Exclude train chips intersecting official section polygons OR containing frozen ARNOLD_ARBORETUM inventory points. Validation/test unchanged.',
                'boundary_url':BOUNDARY,'boundary_sha256':hashlib.sha256(payload).hexdigest(),
                'excluded':excluded, 'excluded_labels':int(labels.chip_id.isin(ids).sum()),
                'original_boston_train_chips':int((chips.split=='train').sum()), 'input_sha256':{}}
    for city in ['ussfo','usbos']:
        cfg = json.loads((parent / f'config-{city}.json').read_text())
        for p in [parent / f'config-{city}.json',
                  *[root/'chips'/cfg['dataset']/name for name in ['chips.parquet','labels.parquet','normalization.json']]]:
            manifest['input_sha256'][p.relative_to(root).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    output = root / 'run-inputs' / experiment
    shutil.copytree(root / 'run-inputs' / SOURCE, output)
    assert not (output/'warm-start.json').exists()
    (output/'arboretum-sections.geojson').write_bytes(payload)
    (output/'ablation.json').write_text(json.dumps(manifest,indent=2))
    with tarfile.open(root/f'{experiment}-inputs.tar.gz','w:gz') as archive:
        archive.add(output,arcname=experiment)
    print(json.dumps({'excluded_train_chips':len(ids),'excluded_labels':manifest['excluded_labels'],
                      'remaining_boston_train':manifest['original_boston_train_chips']-len(ids)}))


def run(root, experiment):
    from urban_tree_ml.config import ProjectConfig
    from urban_tree_ml.joint import combine_manifests, train_and_evaluate
    manifest = json.loads((root/'run-inputs'/experiment/'ablation.json').read_text())
    for name,digest in manifest['input_sha256'].items():
        assert hashlib.sha256((root/name).read_bytes()).hexdigest()==digest, name
    ids = {r['chip_id'] for r in manifest['excluded']}
    configs, sources = [], {}
    run_dir = root/'runs'/experiment
    run_dir.mkdir(parents=True,exist_ok=False)
    shutil.copy2(root/'runs'/SOURCE/'curation-audit.json',run_dir/'curation-audit.json')
    shutil.copy2(root/'run-inputs'/experiment/'ablation.json',run_dir/'ablation.json')
    for city in ['ussfo','usbos']:
        cfg = ProjectConfig.model_validate_json((root/'runs'/SOURCE/f'config-{city}.json').read_text())
        original = root/'chips'/cfg.dataset
        destination = root/'chips'/f'{experiment}-{city}'
        destination.mkdir(parents=True,exist_ok=False)
        for name in ['chips.parquet','labels.parquet']:
            old = pd.read_parquet(original/name)
            new = filter_training(old,city,ids)
            if name=='chips.parquet':
                new['path'] = new.path.map(lambda p:f'../{original.name}/{p}')
                assert all((destination/p).is_file() for p in new.path)
            new.to_parquet(destination/name,index=False)
        for name in ['normalization.json','normalization-local.json','summary.json']:
            if (original/name).exists():
                shutil.copy2(original/name,destination/name)
        cfg.dataset=destination.name
        cfg.experiment=experiment
        (run_dir/f'config-{city}.json').write_text(cfg.model_dump_json(indent=2))
        configs.append(cfg); sources[city]=destination
    print(combine_manifests(sources,root/'chips'/experiment),flush=True)
    train_and_evaluate(configs,experiment,run_dir)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--experiment',required=True)
    parser.add_argument('--root',type=Path,default=Path(os.environ.get('TREE_ML_DATA_ROOT','.')))
    parser.add_argument('--prepare',action='store_true')
    parser.add_argument('configs',nargs='*')
    args=parser.parse_args()
    if not args.experiment or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-' for c in args.experiment):
        parser.error('Invalid experiment')
    (prepare if args.prepare else run)(args.root,args.experiment)
