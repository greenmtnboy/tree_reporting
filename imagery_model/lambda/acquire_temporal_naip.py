"""Acquire alternate NAIP years over the eight existing city source footprints.

Writes isolated acquisition receipts. Does not change studio/defaults/annotations.
"""
import ctypes
import hashlib
import json
import os
from pathlib import Path
import xml.etree.ElementTree as ET
from urban_tree_ml.config import load_config
from urban_tree_ml.imagery import _sign_asset_href, _stream_download, _validate_raster

ROOT=Path(os.environ['TREE_ML_DATA_ROOT'])
WORK=Path(__file__).resolve().parents[1]
QA=ROOT/'qa/temporal-naip-20261112'


def main():
    if os.name=='nt':
        k=ctypes.windll.kernel32;k.GetCurrentProcess.restype=ctypes.c_void_p
        k.SetProcessAffinityMask.argtypes=[ctypes.c_void_p,ctypes.c_size_t]
        k.SetPriorityClass.argtypes=[ctypes.c_void_p,ctypes.c_ulong]
        assert k.SetProcessAffinityMask(k.GetCurrentProcess(),3)
        assert k.SetPriorityClass(k.GetCurrentProcess(),0x4000)
    for city,state,base,years,cfg in [
        ('usbos','ma','2023/usbos-2023-external.vrt',[2021,2025],'sf_boston_vocab_v2_boston.yaml'),
        ('ussfo','ca','2022/ussfo-2022-mosaic.vrt',[2020,2024],'sf_boston_vocab_v2_sf.yaml')]:
        config=load_config(WORK/'configs'/cfg)
        config.imagery.source_resolution_m=.6
        base_path=ROOT/'imagery'/city/base
        keys={'_'.join(Path(x.text).stem.split('_')[1:5]) for x in ET.parse(base_path).iter('SourceFilename')}
        catalog=QA/(city+'-catalog.json');data=json.loads(catalog.read_text())
        for year in years:
            items=[x for x in data['items'] if str(x['properties'].get('naip:year'))==str(year)
                and x['properties'].get('naip:state')==state
                and '_'.join(x['id'].split('_')[1:5]) in keys]
            if len(items)!=len(keys) or len({ '_'.join(x['id'].split('_')[1:5]) for x in items})!=len(keys):
                raise ValueError(f'Incomplete/ambiguous footprint coverage: {city}/{year}')
            directory=ROOT/'imagery'/city/str(year);directory.mkdir(parents=True,exist_ok=True)
            records=[]
            for item in sorted(items,key=lambda x:x['id']):
                path=directory/(item['id']+'.tif');receipt=path.with_suffix('.manifest.json')
                record={'band_names':[b['common_name'] for b in item['assets']['image']['eo:bands']]}
                href=item['assets']['image']['href']
                if not path.exists():
                    part=path.with_suffix('.download.part')
                    if part.exists(): raise ValueError('Incomplete download exists; inspect before retry')
                    print('Downloading',city,year,item['id'],flush=True)
                    size,expected,digest=_stream_download(_sign_asset_href(href),part)
                    raster=_validate_raster(part,config,record,expected_resolution_m=.6)
                    os.replace(part,path)
                    receipt.write_text(json.dumps(dict(item_id=item['id'],source_href=href,
                        acquisition_datetime=item['properties']['datetime'],bytes=size,sha256=digest,raster=raster),indent=2),encoding='utf-8')
                saved=json.loads(receipt.read_text(encoding='utf-8'))
                with path.open('rb') as f: digest=hashlib.file_digest(f,'sha256').hexdigest()
                if digest!=saved['sha256']: raise ValueError('Existing image checksum differs')
                records.append({'item_id':item['id'],'path':str(path),'sha256':digest,
                    'datetime':item['properties']['datetime'],'gsd_m':item['properties']['gsd']})
                print('Verified',item['id'],flush=True)
            payload=dict(acquisition_id=f'{city}-naip-{year}',city=city,year=year,
                source_grid=str(base_path),source_grid_sha256=hashlib.sha256(base_path.read_bytes()).hexdigest(),
                catalog_sha256=hashlib.sha256(catalog.read_bytes()).hexdigest(),items=records,
                state='downloaded_not_registered',split_policy='reuse geographic reference grid; no per-year random split',
                excluded_catalog_states=[s for s in sorted({i['properties'].get('naip:state','') for i in data['items'] if str(i['properties'].get('naip:year'))==str(year)}) if s!=state])
            (QA/f'{city}-{year}-acquisition.json').write_text(json.dumps(payload,indent=2)+'\n',encoding='utf-8')
            print('Acquisition downloaded',city,year,flush=True)


if __name__=='__main__': main()
