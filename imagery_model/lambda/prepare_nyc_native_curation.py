"""Prepare native NYC 30cm street inventory with geographically sealed splits."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import shutil

import numpy as np
import pandas as pd
from pyproj import Transformer
import rasterio
from rasterio.windows import Window, bounds
from shapely.geometry import shape, box
from shapely.ops import transform
from shapely import contains_xy

from urban_tree_ml.config import ProjectConfig
from urban_tree_ml.inventory import _add_source_label_eligibility, planted_after_imagery
from urban_tree_ml.taxonomy import Taxonomy, encode_taxonomy
from urban_tree_ml.splits import assign_spatial_splits, Block, split_for_block
from urban_tree_ml.chips import build_chips
from urban_tree_ml.prediction_chips import build_prediction_chips
from urban_tree_ml.evaluation import run_evaluation
from urban_tree_ml.quality import build_registration_review
from urban_tree_ml.curation_archive import acquisition
from compare_nyc_native30 import ITEM, PARENT, CHECKPOINT_SHA, digest, save

RUN = 'nyc-naip-native30-v7-baseline'
OLD = 'nyc-naip-swin-30cm-v7-cold-start-v1'


def guarded_window_split(extent, transformer, config):
    """Require the ENTIRE chip inside one block and its interior guard."""
    west,south,east,north = extent.bounds
    xs,ys = transformer.transform([west,east,east,west], [south,south,north,north])
    size,guard = config.split.block_size_m,config.split.guard_m
    blocks = {(math.floor(x/size),math.floor(y/size)) for x,y in zip(xs,ys)}
    if len(blocks) != 1:
        return None
    bx,by = next(iter(blocks))
    if any(min(x-bx*size,(bx+1)*size-x,y-by*size,(by+1)*size-y) < guard for x,y in zip(xs,ys)):
        return None
    return split_for_block(Block(bx,by),config.split,config.seed)


def prepare(root):
    target = root/'run-inputs'/RUN
    if (target/'PREPARED.json').exists():
        return
    old = root/'run-inputs'/OLD
    raw = json.loads((old/'config.json').read_text())
    raster = root/'imagery/usnyc/2023'/(ITEM+'.tif')
    raw.update(experiment=RUN,dataset=RUN+'-usnyc')
    raw['imagery'].update(local_raster=str(raster),source_resolution_m=.3,item_ids=[ITEM],
                          datetime='2023-08-20/2023-08-20',planting_date_cutoff='2023-08-20',boundary_shifts=False)
    raw['training'].update(batch_size=2,workers=0)
    config = ProjectConfig.model_validate(raw)
    target.mkdir(parents=True,exist_ok=True)
    save(target/'config.json',config.model_dump(mode='json'))
    assert digest(raster) == json.loads(raster.with_suffix('.manifest.json').read_text())['sha256']
    vocabulary = json.loads(Path(raw['reference']['taxonomy_path']).read_text())
    frame = pd.read_parquet(old/'municipal-inventory.parquet')
    park_ll = shape(json.loads((old/'central-park.geojson').read_text())['geometry'])
    with rasterio.open(raster) as src:
        xy = Transformer.from_crs('EPSG:4326',src.crs,always_xy=True)
        park = transform(xy.transform,park_ll)
        xs,ys = xy.transform(frame.longitude.to_numpy(),frame.latitude.to_numpy())
        valid = (xs>=src.bounds.left)&(xs<src.bounds.right)&(ys>src.bounds.bottom)&(ys<=src.bounds.top)
        valid &= ~contains_xy(park,xs,ys)
        frame = _add_source_label_eligibility(frame.loc[valid].copy(),config.inventory)
        frame = assign_spatial_splits(frame,config.split,config.seed)
        frame = encode_taxonomy(frame,Taxonomy(**vocabulary))
        frame['genus_eligible'] = frame.taxon_eligible & frame.genus_id.ge(0)
        frame['species_eligible'] = frame.taxon_eligible & frame.species_id.ge(0)
        frame['dbh_log1p'] = np.nan
        frame.loc[frame.dbh_eligible,'dbh_log1p'] = np.log1p(frame.loc[frame.dbh_eligible,'diameter_at_breast_height'])
        # Cutoff remains enforced again by build_chips; never turn these into positives.
        frame['post_imagery_planting'] = planted_after_imagery(frame,config.imagery.planting_date_cutoff)
        xs,ys=xy.transform(frame.longitude.to_numpy(),frame.latitude.to_numpy())
        inv=~src.transform
        n=config.imagery.chip_pixels
        frame['_row']=(np.floor((inv.e*ys+inv.f)/n)*n).astype(int)
        frame['_col']=(np.floor((inv.a*xs+inv.c)/n)*n).astype(int)
        to_split=Transformer.from_crs(src.crs,config.split.projected_crs,always_xy=True)
        windows=[]
        for (row,col), group in frame.groupby(['_row','_col']):
            if row<0 or col<0 or row+n>src.height or col+n>src.width:
                continue
            window=Window(col,row,n,n);extent=box(*bounds(window,src.transform))
            if extent.intersects(park):
                continue
            split=guarded_window_split(extent,to_split,config)
            if split is None:
                continue
            assert set(group.split)=={split} and group.split_eligible.all()
            image=src.read(config.imagery.bands,window=window)
            fraction=float((np.any(image!=0,axis=0)&np.all(src.read_masks(config.imagery.bands,window=window)>0,axis=0)).mean())
            if fraction < config.imagery.minimum_valid_fraction:
                continue
            windows.append(dict(row=int(row),col=int(col),split=split,valid_fraction=fraction,
                chip_id=f'r{row//n:06d}_c{col//n:06d}',inventory_rows=len(group)))
        keys={(w['row'],w['col']) for w in windows}
        frame=frame[[key in keys for key in zip(frame._row,frame._col)]].copy()
        frame=frame.drop(columns=['_row','_col'])
        assert set(frame.split)=={'train','validation','test'}
        frame.to_parquet(target/'inventory.parquet',index=False)
        development=frame[frame.split!='test']
        development.to_parquet(target/'development-inventory.parquet',index=False)
        save(target/'taxonomy.json',vocabulary)
        summary=dict(windows_by_split=pd.DataFrame(windows).groupby('split').size().to_dict(),
            inventory_rows_by_split=frame.groupby('split').size().to_dict(),seed=config.seed,
            block_size_m=config.split.block_size_m,guard_m=config.split.guard_m,
            fractions={'train':.7,'validation':.15,'test':.15},test_evaluated=False,
            test_pixels_materialized=False,central_park_training=False,source_sha256=digest(raster),
            inventory_snapshot_sha256=digest(old/'municipal-inventory.parquet'),windows=windows)
        save(target/'split-plan.json',summary)
        chip_root=root/'chips'/config.dataset
        if not (chip_root/'summary.json').exists():
            print(json.dumps(build_chips(config,raster,inventory_source=target/'development-inventory.parquet',use_default_feedback=False)),flush=True)
        minx,miny,maxx,maxy=park.bounds
        col0,row1=inv*(minx,miny);col1,row0=inv*(maxx,maxy)
        park_windows=[(r,c) for r in range(math.floor(row0/n)*n,math.ceil(row1/n)*n,n)
            for c in range(math.floor(col0/n)*n,math.ceil(col1/n)*n,n)
            if 0<=r and 0<=c and r+n<=src.height and c+n<=src.width and park.intersects(box(*bounds(Window(c,r,n,n),src.transform)))]
        unscored=config.model_copy(deep=True);unscored.dataset=RUN+'-central-park'
        save(target/'config-central-park.json',unscored.model_dump(mode='json'))
        if not (root/'chips'/unscored.dataset/'summary.json').exists():
            build_prediction_chips(unscored,raster,park_windows,labels_template=chip_root/'labels.parquet')
    # Switch only NYC's canonical inventory for the newly requested acquisition;
    # preserve the previous encoded inventory and all frozen 2022 evaluations.
    canonical=root/'inventory/usnyc';canonical.mkdir(parents=True,exist_ok=True)
    previous=old/'encoded-inventory-before-native30.parquet'
    if (canonical/'inventory.parquet').exists() and not previous.exists():
        shutil.copyfile(canonical/'inventory.parquet',previous)
    shutil.copyfile(target/'inventory.parquet',canonical/'inventory.parquet')
    shutil.copyfile(target/'taxonomy.json',canonical/'taxonomy.json')
    acquisition(root,raster,register=True)
    review=root/'qa/registration'/raster.stem
    if not (review/'manifest.json').exists():
        build_registration_review(config,raster,samples=60,window_pixels=512)
    manifest=json.loads((review/'manifest.json').read_text())
    if manifest['metadata'].get('curation_schema_version')!=2:
        if (review/'reviews.json').exists():
            raise ValueError('New NYC workspace unexpectedly has reviews; do not migrate implicitly')
        manifest['metadata'].update(curation_schema_version=2,curation_crs='EPSG:26918',curation_imagery_id=digest(raster))
        save(review/'manifest.json',manifest)
    assert not manifest['metadata']['test_labels_included']
    save(target/'PREPARED.json',dict(config=str(target/'config.json'),review_dir=str(review),
        labeled_chips=pd.read_parquet(chip_root/'chips.parquet').groupby('split').size().to_dict(),
        park_chips=len(park_windows),split_plan=str(target/'split-plan.json')))
    print((target/'PREPARED.json').read_text(),flush=True)


def infer(root):
    import torch
    torch.set_num_threads(4)
    target=root/'run-inputs'/RUN;checkpoint=root/'runs'/PARENT/'checkpoints/007.ckpt'
    assert digest(checkpoint)==CHECKPOINT_SHA
    for filename,split,cohort in [('config.json','validation','validation-usnyc'),
            ('config.json','train','train-usnyc'),('config-central-park.json','train','train-usnyc-central-park')]:
        if (root/'runs'/RUN/'evaluation'/cohort/'inference-coverage.json').exists():
            continue
        config=ProjectConfig.model_validate(json.loads((target/filename).read_text()))
        config.training.batch_size=1
        print('Inference '+cohort,flush=True)
        result=run_evaluation(config,checkpoint,split=split,cohort=cohort,device_name='cpu',output_run=root/'runs'/RUN)
        print(json.dumps(dict(cohort=cohort,chips=result['chips'])),flush=True)
    (root/'runs'/RUN/'COMPLETE').write_text('Native30 frozen v7 baseline inference completed; no training; test sealed.\n')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--stage',choices=['prepare','infer','all'],default='all')
    args=parser.parse_args()
    if args.stage in ('prepare','all'):prepare(args.root)
    if args.stage in ('infer','all'):infer(args.root)
