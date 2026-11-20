"""Align downloaded acquisitions to the reference grid and create empty review workspaces.

Published curation is never copied here. Peer copying is an explicit UI action.
"""
import copy
import ctypes
import hashlib
import json
import os
from pathlib import Path
import xml.etree.ElementTree as ET
import numpy as np
import pandas as pd
import rasterio
from rasterio.vrt import WarpedVRT
from rasterio.shutil import copy as raster_copy
from rasterio.enums import Resampling
from rasterio.windows import Window
from pyproj import Transformer
from PIL import Image
from urban_tree_ml.config import load_config
from urban_tree_ml.quality import render_registration_review_html

ROOT=Path(os.environ['TREE_ML_DATA_ROOT'])
WORK=Path(__file__).resolve().parents[1]
QA=ROOT/'qa/temporal-naip-20261112'
BASES={'usbos':('2023/usbos-2023-external.vrt','usbos-2023-external','boston_naip_external.yaml',2023),
       'ussfo':('2022/ussfo-2022-mosaic.vrt','ussfo-2022-mosaic','sf_naip_citywide_curated.yaml',2022)}
def read(p): return json.loads(p.read_text(encoding='utf-8'))
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,v): p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(v,indent=2)+'\n',encoding='utf-8')


def prepare(acquisition):
    city=acquisition['city'];year=acquisition['year'];identity=acquisition['acquisition_id']
    base,review_name,config_name,base_year=BASES[city]
    reference=ROOT/'imagery'/city/base
    if sha(reference)!=acquisition['source_grid_sha256']: raise ValueError('Reference grid drift')
    config=load_config(WORK/'configs'/config_name)
    split_policy=dict(seed=config.seed,splits=config.split.model_dump(mode='json'),reference_grid_sha256=sha(reference))
    split_sha=hashlib.sha256(json.dumps(split_policy,sort_keys=True).encode()).hexdigest()
    directory=ROOT/'run-inputs'/identity
    if (directory/'READY').exists(): return read(directory/'acquisition.json')
    if directory.exists(): raise ValueError('Partial preparation exists; inspect before retry')
    directory.mkdir(parents=True)
    vrt=directory/'imagery.vrt'
    with rasterio.open(reference) as grid:
        width,height=grid.width,grid.height
        tree=ET.Element('VRTDataset',rasterXSize=str(width),rasterYSize=str(height))
        ET.SubElement(tree,'SRS').text=grid.crs.to_wkt()
        ET.SubElement(tree,'GeoTransform').text=','.join(map(str,grid.transform.to_gdal()))
        warps=[]
        for item in acquisition['items']:
            path=Path(item['path'])
            # Download receipts already bind bytes; verify the receipt identity,
            # avoiding a second complete multi-GB scan during virtual prep.
            if read(path.with_suffix('.manifest.json'))['sha256']!=item['sha256']: raise ValueError('Asset receipt drift')
            warped=directory/(item['item_id']+'.vrt')
            with rasterio.open(path) as source:
                with WarpedVRT(source,crs=grid.crs,transform=grid.transform,width=width,height=height,
                               resampling=Resampling.bilinear,nodata=0) as aligned:
                    raster_copy(aligned,warped,driver='VRT')
            warps.append(warped)
        for band,color in enumerate(['Red','Green','Blue','Undefined'],1):
            output=ET.SubElement(tree,'VRTRasterBand',dataType='Byte',band=str(band))
            ET.SubElement(output,'ColorInterp').text=color
            ET.SubElement(output,'NoDataValue').text='0'
            for warped in warps:
                src=ET.SubElement(output,'ComplexSource')
                ET.SubElement(src,'SourceFilename',relativeToVRT='1').text=warped.name
                ET.SubElement(src,'SourceBand').text=str(band)
                for name in ['SrcRect','DstRect']: ET.SubElement(src,name,xOff='0',yOff='0',xSize=str(width),ySize=str(height))
                ET.SubElement(src,'NODATA').text='0'
        ET.ElementTree(tree).write(vrt,encoding='utf-8',xml_declaration=True)
    config.dataset=identity;config.experiment=identity;config.imagery.local_raster=vrt
    config.imagery.acquisition_id=identity
    config.imagery.resolution_m=.6;config.imagery.source_resolution_m=.6
    config.imagery.datetime=f'{year}-01-01/{year}-12-31'
    config.imagery.item_ids=[x['item_id'] for x in acquisition['items']]
    config.targets.background_mode='curated_only'
    config_path=directory/'config.json';save(config_path,config.model_dump(mode='json'))
    review=ROOT/'qa/registration'/identity;(review/'images').mkdir(parents=True)
    original=read(ROOT/'qa/registration'/review_name/'manifest.json')
    manifest=copy.deepcopy(original)
    manifest['metadata'].update(review_id=identity,source_raster=str(vrt),curation_imagery_id=sha(vrt),
        acquisition_id=identity,acquisition_year=year,curation_crs=grid.crs.to_string(),
        split_policy_sha256=split_sha,peer_copy_required=True,test_labels_included=False)
    samples={str(s['sample_id']):s for s in manifest['samples']}
    reference_manifest=ROOT/'chips'/('sf-boston-nyc-fairfax-naip-swin-30cm-v20-curated-finetune-'+city)/'chips.parquet'
    reference_rows=pd.read_parquet(reference_manifest,columns=['chip_id','split'])
    if reference_rows.chip_id.duplicated().any(): raise ValueError('Duplicate reference chip membership')
    reference_splits=dict(zip(reference_rows.chip_id,reference_rows.split))
    manifest['metadata']['reference_split_manifest_sha256']=sha(reference_manifest)
    kept=[];kept_samples=set();omitted=[]
    with rasterio.open(vrt) as image:
        forward=Transformer.from_crs('EPSG:4326',image.crs,always_xy=True)
        for scene in manifest['scenes']:
            chip=scene.get('validation_chip_id')
            if chip in reference_splits:
                scene['split']=reference_splits[chip]
                scene['splits']=[reference_splits[chip]]
            if 'test' in scene.get('splits',[]): omitted.append(scene['scene_id']);continue
            anchor=scene.get('anchor') or next((samples[str(i)] for i in scene['sample_ids']),None)
            if anchor is None: raise ValueError('Scene has no geographic anchor')
            x,y=forward.transform(anchor['longitude'],anchor['latitude'])
            col,row=(~image.transform)*(x,y)
            left,top=col-anchor['target_x'],row-anchor['target_y']
            if abs(left-round(left))>.05 or abs(top-round(top))>.05: raise ValueError('Scene does not align with reference grid')
            raw=image.read(window=Window(round(left),round(top),scene['image_width'],scene['image_height']),boundless=True,fill_value=0)
            valid=float(np.any(raw!=0,axis=0).mean())
            if valid<.98: omitted.append(scene['scene_id']);continue
            path=review/scene['image'];path.parent.mkdir(parents=True,exist_ok=True)
            Image.fromarray(np.moveaxis(raw[:3],0,-1)).save(path)
            scene['valid_fraction']=valid;scene['source_item_ids']=config.imagery.item_ids
            kept.append(scene);kept_samples.update(map(str,scene['sample_ids']))
    manifest['scenes']=kept
    manifest['samples']=[s for s in manifest['samples'] if str(s['sample_id']) in kept_samples]
    for sample in manifest['samples']:
        sample.pop('vegetation_heuristic',None);sample['source_item_ids']=config.imagery.item_ids
    manifest['metadata'].update(rendered_scenes=len(kept),rendered_samples=len(manifest['samples']))
    if not kept: raise ValueError('No valid development scenes')
    save(review/'manifest.json',manifest)
    (review/'index.html').write_text(render_registration_review_html(review),encoding='utf-8')
    record=dict(id=identity,city=city,label=f'{city.upper()} NAIP {year}',date=f'{year}-07-01',
        date_precision='year; nearest chronological year selection',state='ready',config_path=str(config_path),raster=str(vrt),
        review_dir=str(review),split_policy_sha256=split_sha,reference_grid=str(reference),
        scenes=len(kept),omitted_nodata_scenes=omitted,training_admission='published human curation only',
        validation_admission='full-tile more_done only',download_receipt=acquisition,
        source_dates=sorted({i['datetime'] for i in acquisition['items']}))
    save(directory/'acquisition.json',record);(directory/'READY').write_text('Prepared imagery and empty curation; no training admitted yet.\n')
    return record


def main():
    if os.name=='nt':
        k=ctypes.windll.kernel32;k.GetCurrentProcess.restype=ctypes.c_void_p
        k.SetProcessAffinityMask.argtypes=[ctypes.c_void_p,ctypes.c_size_t];k.SetPriorityClass.argtypes=[ctypes.c_void_p,ctypes.c_ulong]
        assert k.SetProcessAffinityMask(k.GetCurrentProcess(),3);assert k.SetPriorityClass(k.GetCurrentProcess(),0x4000)
    records=[]
    for path in sorted(QA.glob('*-acquisition.json')):
        record=prepare(read(path));records.append(record);print(record['id'],record['scenes'],'scenes ready',flush=True)
    for city,(base,review,config_name,year) in BASES.items():
        peers=[r for r in records if r['city']==city]
        if not peers: continue
        records.append(dict(id=f'{city}-naip-{year}',city=city,label=f'{city.upper()} NAIP {year} (original)',
            date=f'{year}-07-01',state='ready',config_path=str(WORK/'configs'/config_name),
            raster=str(ROOT/'imagery'/city/base),review_dir=str(ROOT/'qa/registration'/review),
            split_policy_sha256=peers[0]['split_policy_sha256']))
    # Explicitly prepared registry is the only activation point; no live edits.
    target=ROOT/'qa/temporal-acquisitions.json'
    if target.exists(): raise ValueError('Registry already exists; merge verified acquisitions explicitly')
    save(target,dict(schema_version=1,acquisitions=records))


if __name__=='__main__': main()
