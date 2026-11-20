"""Paired CPU detection and complete explicit-region validation audit; no training."""
import argparse
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
import torch

from diagnose_taxonomy_loss import sha
from score_confidence_sweep import sweep, CITY_COHORTS
from score_single_tree_occupancy import summarize
from urban_tree_ml.evaluation import _decode_batch, _attribute_metrics
from urban_tree_ml.crown_center_score import crown_matches
from urban_tree_ml.sweep_history import policy_truth, POLICIES
from urban_tree_ml.model import RawImageryTreeModel


def main(root, output):
    torch.set_num_threads(8)
    run='sf-boston-nyc-naip-swin-30cm-v16-curated-finetune'
    source=root/'runs'/run
    base=root/'benchmarks/v15-vs-v16-single-tree-occupancy'
    sample=root/'benchmarks/v16-taxonomy-loss-cpu-sample'
    assert (base/'COMPLETE').exists() and (sample/'COMPLETE').exists()
    output.mkdir(parents=True,exist_ok=False)
    selected=pd.read_parquet(sample/'windows.parquet')
    primary=set(zip(selected.city,selected.chip_id))
    groups={}; files={}; all_rows=[]; truths={}; hashes={}
    for city,cohort in CITY_COHORTS.items():
        directory=base/'chips'/(city+'-groups')
        manifest=pd.read_parquet(directory/'chips.parquet')
        for row in manifest.itertuples():
            key=(city,row.chip_id); path=directory/row.path
            with np.load(path) as z:
                g=z['single_tree_groups'].squeeze()
                ids=np.unique(g[g>0])
                if len(ids): groups[key]=(g.copy(),ids)
            if key in primary or len(ids):
                files[key]=path
                all_rows.append(dict(city=city,chip_id=row.chip_id,primary=key in primary,has_groups=bool(len(ids))))
                hashes[str(path)]=sha(path)
        truthpath=source/'evaluation'/cohort/'ground-truth.parquet'
        truths[city]=pd.read_parquet(truthpath)
        hashes[str(truthpath)]=sha(truthpath)
    assert len(groups)==8 and sum(len(ids) for _,ids in groups.values())==21
    windows=pd.DataFrame(all_rows)
    assert int(windows.primary.sum())==155
    windows.to_parquet(output/'windows.parquet',index=False)
    taxonomy=json.loads((source/'vegetation-taxonomy.json').read_text())
    cfg=json.loads((source/'config-ussfo.json').read_text())
    normpath=root/'chips'/(run+'-ussfo')/'normalization.json'
    norm=json.loads(normpath.read_text());hashes[str(normpath)]=sha(normpath)
    for city in CITY_COHORTS:
        other=json.loads((root/'chips'/(run+'-'+city)/'normalization.json').read_text())
        assert all(norm[k]==other[k] for k in ('mean','std'))
    mean=torch.tensor(norm['mean'],dtype=torch.float32)[:,None,None]
    std=torch.tensor(norm['std'],dtype=torch.float32)[:,None,None]
    models={}; checkpoints={}
    for label,filename in [('parent','parent-baseline.ckpt'),('best','001-0000848.ckpt'),('final','last.ckpt')]:
        path=source/'checkpoints'/filename
        c=torch.load(path,map_location='cpu',weights_only=False)
        model=RawImageryTreeModel(**dict(cfg['model'],pretrained=False),
              genus_classes=len(taxonomy['genera']),species_classes=len(taxonomy['species'])).eval()
        model.load_state_dict({k.removeprefix('network.'):v for k,v in c['state_dict'].items()},strict=True)
        models[label]=model; checkpoints[label]=dict(path=str(path),sha256=sha(path),step=c['global_step'])
        del c
    assert checkpoints['parent']['sha256']=='3b9e7cab863698c63c812b3dee89f55070134656d5e3803054ec8e3354362a82'
    provenance=dict(precision='CPU fp32',sample=str(sample/'windows.parquet'),
        primary_windows=155,total_inferred_windows=len(windows),all_explicit_validation_groups=21,
        test_evaluated=False,training_performed=False,files=hashes,checkpoints=checkpoints,
        nms_kernel=cfg['evaluation']['nms_kernel'],max_detections=cfg['evaluation']['max_detections_per_chip'])
    (output/'provenance.json').write_text(json.dumps(provenance,indent=2))
    rows=[]; start=time.perf_counter()
    with torch.inference_mode():
        for i in range(0,len(windows),2):
            batch=windows.iloc[i:i+2]; arrays=[]
            for row in batch.itertuples():
                with np.load(files[(row.city,row.chip_id)]) as z:
                    arrays.append({k:z[k].copy() for k in ('image','center','scoring_mask')})
            image=torch.stack([(torch.from_numpy(z['image']).float()-mean)/std for z in arrays])
            targets=torch.from_numpy(np.stack([z['center'] for z in arrays])).float()
            masks=torch.from_numpy(np.stack([z['scoring_mask'] for z in arrays])).float()
            for label,model in models.items():
                pred=model(image)
                decoded=_decode_batch(pred,batch.chip_id.tolist(),
                    max_detections_per_chip=provenance['max_detections'],nms_kernel=provenance['nms_kernel'],
                    target_center=targets,detection_mask=masks)
                # A batch can cross a city boundary; avoid assuming chip IDs globally unique.
                bychip={r.chip_id:r.city for r in batch.itertuples()}
                assert len(bychip)==len(batch)
                rows.extend(dict(model=label,city=bychip[r['chip_id']],**r) for r in decoded)
                del pred
            if i%20==0 or i+2>=len(windows):
                pd.DataFrame(rows).to_parquet(output/'predictions.parquet',index=False)
                print(f'{min(i+2,len(windows))}/{len(windows)} windows, {time.perf_counter()-start:.1f}s',flush=True)
    predictions=pd.DataFrame(rows)
    metrics=[];best=[];attributes=[]
    for city in CITY_COHORTS:
        ids={chip for c,chip in primary if c==city}
        truth=truths[city][truths[city].chip_id.isin(ids)].reset_index(drop=True)
        for label in models:
            p=predictions[predictions.city.eq(city)&predictions.model.eq(label)&predictions.chip_id.isin(ids)].reset_index(drop=True)
            assert set(p.chip_id)==ids
            for policy in POLICIES:
                curve=[dict(city=city,model=label,policy=policy,**r) for r in sweep(p,truth,.6,policy)]
                metrics.extend(curve)
                best.append(max(curve,key=lambda r:(r['f2'],r['threshold'])))
            for threshold in (.13,.35):
                chosen=p[p.score>=threshold]
                matches=crown_matches(chosen,truth,.6)
                a=_attribute_metrics(chosen,truth,matches,dbh_tolerance_in=2)
                attributes.append(dict(city=city,model=label,threshold=threshold,attributes=a))
    occupancy=[]
    for city in CITY_COHORTS:
        for label in models:
            p=predictions[predictions.city.eq(city)&predictions.model.eq(label)]
            for threshold in sorted(set([.12,.13,.14,.35])):
                counts=[]
                for (c,chip),(g,ids) in groups.items():
                    if c!=city: continue
                    selected=p[p.chip_id.eq(chip)&p.score.ge(threshold)]
                    assignment=g[selected.output_y.to_numpy(dtype=int),selected.output_x.to_numpy(dtype=int)]
                    counts.extend(int((assignment==gid).sum()) for gid in ids)
                occupancy.append(dict(city=city,model=label,threshold=threshold,**summarize(counts)))
    for name,value in [('sweep',metrics),('best',best),('occupancy',occupancy),('attributes',attributes)]:
        (output/(name+'.json')).write_text(json.dumps(value,indent=2))
    (output/'COMPLETE').write_text('Paired CPU fp32 sampled detection and all explicit validation groups; no training.\n')
    print('COMPLETE',flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();main(a.root,a.output)
