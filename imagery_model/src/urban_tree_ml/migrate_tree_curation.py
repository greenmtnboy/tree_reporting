"""Stage a lossless schema-2 migration; --apply requires the reviewer stopped."""
import argparse
import hashlib
import json
import shutil
from pathlib import Path

import pandas as pd
import rasterio

from urban_tree_ml.config import load_config
from urban_tree_ml.feedback import _write_json_atomic,load_persisted_reviews,snapshot_registration_annotations
from urban_tree_ml.quality import append_validation_chip_to_registration_review
from urban_tree_ml.tree_curation import storage_payload,tree_records,expand_tree_records


def migrate(config, directory, stage, apply=False):
    if stage.exists():raise ValueError('Choose an unused staging directory')
    stage.mkdir(parents=True)
    before={name:(directory/name).read_bytes() for name in ['manifest.json','reviews.json','training-feedback.json'] if (directory/name).exists()}
    for name,raw in before.items():(stage/('before-'+name)).write_bytes(raw)
    manifest=json.loads(before['manifest.json'])
    if manifest['metadata'].get('curation_schema_version')==2:raise ValueError('Already migrated')
    state=load_persisted_reviews(directory)
    raster=config.imagery.local_raster
    with rasterio.open(raster) as src:crs=src.crs.to_string()
    manifest['metadata'].update(curation_schema_version=2,curation_crs=crs)
    sidecar=raster.with_suffix('.manifest.json')
    manifest['metadata']['curation_imagery_id']=hashlib.sha256(sidecar.read_bytes()).hexdigest()
    canonical=storage_payload({'metadata':manifest['metadata'],'reviews':state['reviews'],
                              'scene_reviews':state['scene_reviews'],'mask_regions':state['mask_regions']},manifest)
    trees_before=canonical['tree_reviews'].copy()
    inventory=pd.read_parquet(config.paths.root/'inventory'/config.inventory.city.lower()/'inventory.parquet')
    # Expand existing large views to all trees, preserving IDs and both review passes.
    for scene in list(manifest['scenes']):
        if scene.get('validation_chip_id'):
            append_validation_chip_to_registration_review(config,raster,stage,scene['validation_chip_id'],pd.DataFrame(),
                _manifest=manifest,_persist=False,_inventory=inventory)
    _write_json_atomic(stage/'manifest.json',manifest)
    canonical['metadata']=manifest['metadata']
    _write_json_atomic(stage/'reviews.json',canonical)
    restored=load_persisted_reviews(stage)
    assert restored['scene_reviews']==state['scene_reviews']
    recovered=tree_records(restored['reviews'],manifest)
    assert recovered==trees_before,'Tree curation changed during projection roundtrip'
    assert len(restored['mask_regions'])==len(state['mask_regions'])
    report={'trees':len(trees_before),'sample_views':len(manifest['samples']),
            'scenes':len(manifest['scenes']),'completion_flags':len(state['scene_reviews']),
            'geographic_masks':len(canonical['mask_regions']),
            'before_sha256':{n:hashlib.sha256(b).hexdigest() for n,b in before.items()},'applied':apply}
    _write_json_atomic(stage/'migration-report.json',report)
    if apply:
        if any((directory/n).read_bytes()!=b for n,b in before.items()):raise ValueError('Live state changed; refusing migration')
        for image in (stage/'images').glob('*.png'):
            target=directory/'images'/image.name
            if target.exists():
                # Preserve the previous rendering as well as its manifest.
                old=stage/'before-images'/image.name;old.parent.mkdir(exist_ok=True);shutil.copy2(target,old)
            shutil.copy2(image,target)
        _write_json_atomic(directory/'manifest.json',manifest)
        _write_json_atomic(directory/'reviews.json',canonical)
        snapshot_registration_annotations(config,raster,review_dir=directory)
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--apply',action='store_true');p.add_argument('--stage',type=Path,required=True)
    args=p.parse_args()
    for city,filename,review in [('ussfo','sf_naip_citywide_curated.yaml','ussfo-2022-mosaic'),('usbos','boston_naip_external.yaml','usbos-2023-external')]:
        cfg=load_config(Path('configs')/filename)
        print(city,migrate(cfg,cfg.paths.root/'qa/registration'/review,args.stage/city,args.apply),flush=True)
