"""Conservative metadata-only protected-chip calculation for source audit."""
import json
from pathlib import Path
import numpy as np
import pandas as pd
import rasterio
from pyproj import Transformer

root=Path('C:/Users/ethan/coding_projects/sf_tree_reporting/imagery_model/artifacts')
out=root/'benchmarks/sf-source-audit-20260925T125849Z'
s=json.loads((out/'reviews.json').read_text());m=json.loads((out/'manifest.json').read_text())
samples={x['sample_id']:x for x in m['samples']};reviews=s['tree_reviews']
chips=pd.read_parquet(root/'chips/sf-boston-naip-swin-v7-finetune-ussfo/chips.parquet')
chips=chips[chips.split.isin(['train','validation'])].copy() # no test evaluation
with rasterio.open(root/'imagery/ussfo/2022/ussfo-2022-mosaic.vrt') as raster:
    affine=raster.transform;crs=raster.crs
project=Transformer.from_crs('EPSG:4326',crs,always_xy=True)
scene_project=Transformer.from_crs(m['metadata']['curation_crs'],crs,always_xy=True)
scene_forward=Transformer.from_crs('EPSG:4326',m['metadata']['curation_crs'],always_xy=True)
cx,cy=affine*(chips.column_offset.to_numpy(),chips.row_offset.to_numpy())
ex,ey=affine*(chips.column_offset.to_numpy()+256,chips.row_offset.to_numpy()+256)
left=np.minimum(cx,ex);right=np.maximum(cx,ex);bottom=np.minimum(cy,ey);top=np.maximum(cy,ey)
hit=np.zeros(len(chips),dtype=bool)
def protect(x,y,r=0):
    global hit
    hit|=(right>=x-r)&(left<=x+r)&(top>=y-r)&(bottom<=y+r)
for scene in m['scenes']:
    ss=[samples[i] for i in scene['sample_ids']]
    if not ss:continue
    done=s['scene_reviews'].get(scene['scene_id'],{})
    if not (done.get('done') or done.get('more_done') or any(reviews.get(str(t['tree_id']),{}).get('source')=='human' for t in ss)):continue
    t=ss[0];x,y=scene_forward.transform(t['longitude'],t['latitude'])
    a,b,d,e=[t.get('transform_'+k,0) for k in ('a','b','d','e')]
    x0=x-a*t['target_x']-b*t['target_y'];y0=y-d*t['target_x']-e*t['target_y']
    corners=[scene_project.transform(x0+a*u+b*v,y0+d*u+e*v) for u,v in [(0,0),(scene['image_width'],0),(0,scene['image_height']),(scene['image_width'],scene['image_height'])]]
    l=min(c[0] for c in corners);r=max(c[0] for c in corners);bt=min(c[1] for c in corners);tp=max(c[1] for c in corners)
    hit|=(right>l)&(left<r)&(top>bt)&(bottom<tp)
crop_protected=int(hit.sum())
# Even default stored tree records are conservatively protected here.
for t in samples.values():
    v=reviews.get(str(t['tree_id']))
    if v is None:continue
    protect(*project.transform(t['longitude'],t['latitude']))
    if 'corrected_longitude' in v:protect(*project.transform(v['corrected_longitude'],v['corrected_latitude']),float(v.get('crown_radius_m',0)))
for v in list(s.get('added_trees',{}).values())+s.get('mask_regions',[]):
    protect(*project.transform(v['longitude'],v['latitude']),float(v.get('crown_radius_m',v.get('radius_m',0))))
chips['protected']=hit
chips.to_csv(out/'non-test-chip-protection.csv',index=False)
result={'crop_footprint_protected':crop_protected,'policy':'Any touched/done crop intersection, any stored inventory review at original/corrected position, or manual crown/mask bbox intersection; no test pixels or labels evaluated','splits':{k:{'total':len(v),'protected':int(v.protected.sum()),'untouched_candidates':int((~v.protected).sum())} for k,v in chips.groupby('split')}}
(out/'untouched-summary.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
