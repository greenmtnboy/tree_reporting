"""Read-only training-donor scouting. Produces review proposals, never labels."""
import argparse
import json
from pathlib import Path
from urllib.parse import urlencode

import numpy as np
import pandas as pd
import rasterio
from rasterio.windows import Window
from PIL import Image, ImageDraw
from urban_tree_ml.crown_center_score import tolerance


def build(root, run, output):
    output.mkdir(parents=True, exist_ok=False)
    d = root / 'runs' / run / 'evaluation/train-usbos'
    cfg = json.loads((d / 'evaluation-metadata.json').read_text())['config']
    chips = pd.read_parquet(root / 'chips' / cfg['dataset'] / 'chips.parquet')
    chips = chips[chips.split == 'train'].set_index('chip_id')
    truth = pd.read_parquet(d / 'ground-truth.parquet')
    pred = pd.read_parquet(d / 'predictions.parquet')
    pred = pred[pred.score >= .13]
    tg = dict(tuple(truth.groupby('chip_id')))
    rows = []
    for chip, p in pred.groupby('chip_id'):
        assert chip in chips.index
        t = tg.get(chip, truth.iloc[:0]); used = np.zeros(len(t), bool)
        xy = t[['output_x', 'output_y']].to_numpy()
        radii = np.array([tolerance(v) / .6 for v in t.crown_radius_m])
        hits = fp = ignored = 0
        for v in p.sort_values('score', ascending=False, kind='stable').itertuples():
            distance = np.hypot(xy[:, 0]-v.output_x, xy[:, 1]-v.output_y)
            eligible = (~used) & (distance <= radii)
            if eligible.any():
                used[np.argmin(np.where(eligible, distance, np.inf))] = True; hits += 1
            elif v.detection_mask_value <= 0: ignored += 1
            else: fp += 1
        rows.append(dict(chip_id=chip, targets=len(t), predictions=len(p), matched=hits,
                         ignored=ignored, precision=hits/max(1,hits+fp), recall=hits/max(1,len(t)),
                         goal_f2=5*hits/max(1,5*hits+4*(len(t)-hits)+fp),
                         confidence_median=float(p.score.median()),
                         confident=int((p.score>=.35).sum()),
                         crown_area_proxy=float(np.nansum(np.pi*t.crown_radius_m**2)/153.6**2)))
    frame = pd.DataFrame(rows)
    # Separate hypotheses: good retained-target fits vs unverified grassy extras.
    canopy = frame[frame.predictions>=100].sort_values(
        ['confident','confidence_median'], ascending=False).head(180)
    grass = frame[frame.targets<=35].sort_values(['confident','ignored'],ascending=False).head(100)
    raster = root/'imagery/usbos/2023'/Path(cfg['imagery']['local_raster']).name
    previews = {}; all_rows = []
    with rasterio.open(raster) as src:
        for chip in set(canopy.chip_id)|set(grass.chip_id):
            c=chips.loc[chip]
            a=src.read(window=Window(int(c.column_offset),int(c.row_offset),512,512),out_shape=(4,128,128)).astype(float)
            ndvi=(a[3]-a[0])/np.maximum(a[3]+a[0],1)
            gray=a[:3].mean(axis=0); gy,gx=np.gradient(gray)
            # Smooth green is a lawn candidate, NOT confirmed absence of trees.
            smooth=(ndvi>.05)&(np.hypot(gx,gy)<4)
            p=pred[pred.chip_id==chip]; xx=(p.output_x.to_numpy()/2).astype(int).clip(0,127); yy=(p.output_y.to_numpy()/2).astype(int).clip(0,127)
            r=frame[frame.chip_id==chip].iloc[0].to_dict()
            r.update(vegetation_fraction=float((ndvi>.05).mean()),smooth_green_fraction=float(smooth.mean()),
                     smooth_green_predictions=int(smooth[yy,xx].sum()))
            all_rows.append(r)
            previews[chip]=Image.fromarray(np.moveaxis(a[:3],0,-1).clip(0,255).astype('uint8')).resize((256,256))
    measured=pd.DataFrame(all_rows)
    measured['textured_vegetation_fraction']=measured.vegetation_fraction-measured.smooth_green_fraction
    good=measured[measured.chip_id.isin(canopy.chip_id)].sort_values(['textured_vegetation_fraction','confident'],ascending=False).head(16)
    lawn=measured[measured.chip_id.isin(grass.chip_id)].sort_values('smooth_green_predictions',ascending=False).head(16)
    result=[]
    for tag, selected in [('canopy',good),('grass',lawn)]:
        sheet=Image.new('RGB',(4*320,max(1,(len(selected)+3)//4)*305),'white'); draw=ImageDraw.Draw(sheet)
        for i,(_,r) in enumerate(selected.iterrows()):
            chip=r.chip_id; x=(i%4)*320;y=(i//4)*305
            sheet.paste(previews[chip],(x,y));draw.text((x,y+257),f'{i+1}: {chip}\nT{r.targets} P{r.predictions} F2 {r.goal_f2:.2f}\nvegetation {r.vegetation_fraction:.2f}',fill='black')
            previews[chip].save(output/(chip+'.png'))
            record=r.to_dict();record['tag']=tag
            record['url']='http://127.0.0.1:8765/curate-training?'+urlencode(dict(city='usbos',chip=chip,run=run+'::train-usbos',threshold=.13))
            result.append(record)
        sheet.save(output/(tag+'-contact.png'))
    (output/'candidates.json').write_text(json.dumps(dict(run=run,threshold=.13,policy='Training only; frozen target fit and image heuristics, not verified canopy/lawn ground truth. No annotation writes.',items=result),indent=2))
    measured.to_json(output/'screened.json',orient='records',indent=2)
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--run',required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();build(a.root,a.run,a.output)
