"""Read-only translation screen; never changes curation or official scores."""
import json
from pathlib import Path
from urllib.parse import urlencode
import numpy as np
import pandas as pd
from pyproj import Transformer
import rasterio

ROOT=Path('C:/Users/ethan/coding_projects/sf_tree_reporting/imagery_model/artifacts')
RUN='sf-boston-naip-swin-v3-finetune'


def pairs(t,p,shift=(0,0),radius=2):
    dist=np.linalg.norm(t[:,None,:]+shift-p[None,:,:],axis=2)
    ti,pi=np.where(dist<=radius)
    order=np.argsort(dist[ti,pi]); usedt=set();usedp=set();out=[]
    for k in order:
        a,b=int(ti[k]),int(pi[k])
        if a not in usedt and b not in usedp:
            usedt.add(a);usedp.add(b);out.append((a,b))
    return out


def analyze(t,p):
    # Fit on alternating spatially ordered labels; check the other half independently.
    t=t[np.lexsort((t[:,1],t[:,0]))]
    fit=t[::2];check=t[1::2]
    delta=(p[None,:,:]-fit[:,None,:]).reshape(-1,2)
    delta=delta[np.linalg.norm(delta,axis=1)<=10]
    if not len(delta):return
    cells,counts=np.unique(np.round(delta).astype(int),axis=0,return_counts=True)
    seeds=cells[np.argsort(counts)[-12:]]
    candidates=[]
    for shift in seeds:
        for _ in range(2):
            match=pairs(fit,p,shift)
            if not match:break
            shift=np.median([p[b]-fit[a] for a,b in match],axis=0)
        matches=pairs(fit,p,shift)
        residual=np.median([np.linalg.norm(fit[a]+shift-p[b]) for a,b in matches]) if matches else 99
        candidates.append((len(matches),-residual,shift))
    _,_,shift=max(candidates,key=lambda v:v[:2])
    if not 2<=np.linalg.norm(shift)<=10:return
    before=pairs(t,p);after=pairs(t,p,shift)
    gain=len(after)-len(before)
    holdgain=len(pairs(check,p,shift))-len(pairs(check,p))
    if gain<6 or holdgain<3 or len(after)<10:return
    residual=np.median([np.linalg.norm(t[a]+shift-p[b]) for a,b in after])
    # How many large translations, well away from the chosen one, work nearly as well?
    controls=[(x,y) for x in range(-10,11,2) for y in range(-10,11,2)
              if 3<=np.hypot(x,y)<=10 and np.linalg.norm(np.array([x,y])-shift)>=4]
    alternatives=[len(pairs(t,p,s)) for s in controls]
    return dict(targets=len(t),predictions=len(p),before=len(before),after=len(after),gain=gain,
                check_gain=holdgain,east_m=round(float(shift[0]),2),north_m=round(float(-shift[1]),2),
                residual_m=round(float(residual),2),alternative_best=max(alternatives,default=0),
                alternative_fraction=round(float(np.mean(np.array(alternatives)>=len(after)-2)),3))


def main():
    results=[];scanned=0
    for city,review in [('ussfo','ussfo-2022-mosaic'),('usbos','usbos-2023-external')]:
        directory=ROOT/'qa/registration'/review
        state=json.loads((directory/'reviews.json').read_text())
        manifest=json.loads((directory/'manifest.json').read_text())
        records=state.get('tree_reviews',{})
        raster=ROOT/'imagery'/city/('2022' if city=='ussfo' else '2023')/(review+'.vrt')
        with rasterio.open(raster) as src:
            inverse=~src.transform
            forward=Transformer.from_crs('EPSG:4326',src.crs,always_xy=True)
        scenes={s.get('validation_chip_id'):s for s in manifest['scenes']}
        for cohort in ['train-'+city,'validation' if city=='ussfo' else 'validation-usbos']:
            d=ROOT/'runs'/RUN/'evaluation'/cohort
            truth=pd.read_parquet(d/'ground-truth.parquet');pred=pd.read_parquet(d/'predictions.parquet')
            pred=pred[pred.score>=0.4];pg=dict(tuple(pred.groupby('chip_id')))
            older=ROOT/'runs/sf-boston-naip-swin-v2-finetune/evaluation'/cohort/'predictions.parquet'
            old=pd.read_parquet(older);old=old[old.score>=.4];oldg=dict(tuple(old.groupby('chip_id')))
            config=json.loads((ROOT/'runs'/RUN/('config-'+city+'.json')).read_text())
            unit=config['imagery']['resolution_m']*config['targets']['output_stride']
            for chip,g in truth.groupby('chip_id'):
                if chip not in pg or not 12<=len(g)<=150:continue
                pp=pg[chip];p=pp[['output_x','output_y']].to_numpy()*unit
                if not 10<=len(p)<=150:continue
                row,col=map(int,chip.replace('r','').replace('c','').split('_'))
                pts=[];live_edits=0
                for tree in g.itertuples():
                    record=records.get(str(tree.tree_id),{})
                    if record.get('status') in {'not-tree','duplicate','occluded','uncertain'}:continue
                    xy=np.array([tree.output_x,tree.output_y],float)*unit
                    if 'corrected_longitude' in record:
                        x,y=forward.transform(record['corrected_longitude'],record['corrected_latitude'])
                        px,py=inverse*(x,y)
                        xy=np.array([px-col*config['imagery']['chip_pixels'],py-row*config['imagery']['chip_pixels']])*config['imagery']['resolution_m'];live_edits+=1
                    pts.append(xy)
                if len(pts)<12:continue
                scanned+=1;result=analyze(np.array(pts),p)
                if result:
                    if chip in oldg:
                        op=oldg[chip][['output_x','output_y']].to_numpy()*unit
                        result['v2_before']=len(pairs(np.array(pts),op))
                        result['v2_after']=len(pairs(np.array(pts),op,(result['east_m'],-result['north_m'])))
                    run=RUN if cohort=='validation' else RUN+'::'+cohort
                    result.update(city=city,chip=chip,cohort=cohort,live_offsets=live_edits,
                        url='http://127.0.0.1:8765/model?'+urlencode(dict(run=run,city=city,split='train' if cohort.startswith('train') else 'validation',chip=chip,threshold=.4,radius=2.0)))
                    if chip in scenes:
                        scene=scenes[chip]['scene_id'];result['scene']=scene
                        result['review_status']=state.get('scene_reviews',{}).get(scene,{})
                        result['url']='http://127.0.0.1:8765/registration?'+urlencode(dict(city=city,scene=scene,fullscreen=1,run=run,threshold=.4,compare_run='',return_=result['url'])).replace('return_=', 'return=')
                    results.append(result)
            print(city,cohort,'scanned',scanned,'candidates',len(results),flush=True)
    results.sort(key=lambda r:(r['alternative_fraction']<=.05,r['gain'],r['check_gain']),reverse=True)
    output=ROOT/'benchmarks/alignment-candidates-swin-v3';output.mkdir(parents=True,exist_ok=True)
    (output/'candidates.json').write_text(json.dumps(dict(run=RUN,threshold=.4,radius_m=2,scanned=scanned,candidates=results),indent=2))
    print(json.dumps(results[:14],indent=2))

if __name__=='__main__':main()
