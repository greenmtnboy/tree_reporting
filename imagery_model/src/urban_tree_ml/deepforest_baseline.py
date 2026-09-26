"""Inference-only DeepForest release baseline on an existing frozen cohort.

Uses the v1.5.0 reference torchvision RetinaNet construction, strict published
NEON weights, RGB [0,1], and stock ImageNet normalization/resize. No fitting.
https://github.com/weecology/DeepForest/blob/v1.5.0/src/deepforest/models/retinanet.py
"""
import argparse
import hashlib
import json
import os
import shutil
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from urban_tree_ml.config import ProjectConfig
from urban_tree_ml.evaluation import _add_geography, _greedy_matches, inference_coverage
from urban_tree_ml.goal_metrics import detection

REVISION = 'cc21436bc5d572dde8ff5f93c1e71a32f563cace'
MODEL_URL = f'https://huggingface.co/weecology/deepforest-tree/resolve/{REVISION}/NEON.pt'


def box_records(chip_id, boxes, scores, center, mask, stride, resolution):
    records = []
    for box, score in zip(boxes, scores, strict=True):
        xmin, ymin, xmax, ymax = map(float, box)
        if not np.isfinite([*box, score]).all() or xmax <= xmin or ymax <= ymin:
            raise ValueError('Invalid DeepForest box/score')
        x, y = (xmin+xmax)/2/stride, (ymin+ymax)/2/stride
        ix, iy = int(x), int(y)
        if not (0 <= ix < mask.shape[1] and 0 <= iy < mask.shape[0]):
            raise ValueError('Prediction center outside chip')
        # Equivalent-area ellipse radius: geometric, not a learned crown attribute.
        radius = np.sqrt((xmax-xmin)*(ymax-ymin))*resolution/2
        records.append(dict(chip_id=chip_id, output_x=x, output_y=y, score=float(score),
            xmin=xmin, ymin=ymin, xmax=xmax, ymax=ymax,
            crown_radius_m=radius, crown_diameter_m=2*radius,
            crown_radius_source='bounding-box-equivalent-ellipse',
            dbh_log1p=None, dbh_in=None, genus_id=None, genus_confidence=None,
            species_id=None, species_confidence=None, species_top_ids=[],
            genus=None, species=None, center_target=float(center[iy,ix]),
            detection_mask_value=float(mask[iy,ix])))
    return records


def score_at(predictions, truth, scale, threshold, policy):
    from urban_tree_ml.crown_center_score import crown_matches
    p = predictions[predictions.score >= threshold]
    matches = (crown_matches(p, truth, scale) if policy == 'crown-half-radius-2to4m'
               else _greedy_matches(p, truth, radius_output_px=float(policy)/scale))
    ids = {m[0] for m in matches}
    ignored = int(((~p.index.isin(ids)) & (p.detection_mask_value <= 0)).sum())
    return detection(len(matches),len(p)-len(matches)-ignored,len(truth)-len(matches),ignored), matches


def run(root, experiment, prepared_run):
    import torch
    from torchvision.models import ResNet50_Weights
    from torchvision.models.detection import retinanet_resnet50_fpn
    from torchvision.models.detection.retinanet import RetinaNet
    if not torch.cuda.is_available():
        raise RuntimeError('Run on the GPU host, not the reviewer workstation')
    parent = root/'runs'/prepared_run
    if not (parent/'COMPLETE').exists():
        raise ValueError('Reference run incomplete')
    output = root/'runs'/experiment
    output.mkdir(parents=True,exist_ok=False)
    shutil.copytree(root/'run-inputs'/prepared_run,root/'run-inputs'/experiment)
    cache = root/'cache'/'deepforest'/REVISION
    cache.mkdir(parents=True,exist_ok=True)
    weights = cache/'NEON.pt'
    if not weights.exists():
        urllib.request.urlretrieve(MODEL_URL, weights)
    backbone = retinanet_resnet50_fpn(weights=None,
        weights_backbone=ResNet50_Weights.IMAGENET1K_V1).backbone
    model = RetinaNet(backbone, num_classes=1, nms_thresh=.05, score_thresh=.05)
    state = torch.load(weights,map_location='cpu',weights_only=True)
    state = {k.removeprefix('model.'):v for k,v in state.items()}
    model.load_state_dict(state,strict=True)
    model.cuda().eval()
    provenance = dict(model='DeepForest NEON RetinaNet',hf_revision=REVISION,
        checkpoint_sha256=hashlib.sha256(weights.read_bytes()).hexdigest(),
        reference_implementation='DeepForest v1.5.0 torchvision RetinaNet',
        score_floor=.05,nms_iou=.05,detections_per_img=model.detections_per_img,
        prepared_run=prepared_run,training=False,test_evaluated=False,
        attributes_available=[],created_at=datetime.now(UTC).isoformat())
    (output/'baseline.json').write_text(json.dumps(provenance,indent=2))
    curves=[]
    for config_path in sorted(parent.glob('config-*.json')):
        cfg=ProjectConfig.model_validate_json(config_path.read_text())
        city=cfg.inventory.city.lower()
        cohort='validation' if city=='ussfo' else 'validation-usbos'
        source=parent/'evaluation'/cohort
        dest=output/'evaluation'/cohort
        dest.mkdir(parents=True)
        cfg.experiment=experiment
        (output/config_path.name).write_text(cfg.model_dump_json(indent=2))
        chip_root=root/'chips'/cfg.dataset
        manifest=pd.read_parquet(chip_root/'chips.parquet')
        rows=manifest[manifest.split=='validation'].sort_values('chip_id')
        records=[];processed=[]
        for n,row in enumerate(rows.itertuples(index=False)):
            with np.load(chip_root/row.path) as chip:
                image=np.ascontiguousarray(chip['image'][:3],dtype=np.float32)
                if not np.isfinite(image).all() or image.min()<0 or image.max()>1.001:
                    raise ValueError('Expected raw RGB [0,1], not standardized tensors')
                with torch.inference_mode():
                    result=model([torch.from_numpy(image).cuda()])[0]
                records.extend(box_records(row.chip_id,result['boxes'].cpu().numpy(),
                    result['scores'].cpu().numpy(),chip['center'],chip['detection_mask'],
                    cfg.targets.output_stride,cfg.imagery.resolution_m))
                if n < 3:
                    from PIL import Image, ImageDraw
                    preview=Image.fromarray(np.clip(image.transpose(1,2,0)*255,0,255).astype(np.uint8))
                    draw=ImageDraw.Draw(preview)
                    for box,score in zip(result['boxes'].cpu().tolist(),result['scores'].cpu().tolist()):
                        if score >= .35:
                            draw.rectangle(box,outline='orange',width=1)
                    preview.save(dest/f'smoke-{row.chip_id}.png')
            processed.append(row.chip_id)
            if n<3 or (n+1)%50==0:
                print(city,'validation',n+1,'/',len(rows),'detections',len(records),flush=True)
            if n==2:
                pd.DataFrame(records).to_parquet(dest/'smoke-predictions.parquet',index=False)
        if not records:
            raise RuntimeError('No detections in city; inspect rather than publish empty baseline')
        p=pd.DataFrame(records)
        coverage=inference_coverage(manifest,'validation',processed,p)
        reference=json.loads((source/'evaluation-metadata.json').read_text())
        p=_add_geography(p,manifest,cfg,Path(reference['source_raster']))
        p['above_threshold']=p.score>=.35
        p.to_parquet(dest/'predictions.parquet',index=True)
        for name in ('ground-truth.parquet','taxonomy.json'):
            shutil.copy2(source/name,dest/name)
        truth=pd.read_parquet(dest/'ground-truth.parquet')
        provenance[city+'_truth_sha256']=hashlib.sha256((dest/'ground-truth.parquet').read_bytes()).hexdigest()
        scale=cfg.imagery.resolution_m*cfg.targets.output_stride
        by_radius={};matches=[]
        for radius in (2.,4.):
            goal,paired=score_at(p,truth,scale,.35,str(radius))
            by_radius[str(radius)]={'detection':goal,'goal_aligned_detection':goal,
                'attributes_on_matched_detections':{}}
            matches.extend(dict(radius_m=radius,prediction_index=i,tree_id=str(truth.loc[j,'tree_id']),
                distance_m=d*scale) for i,j,d in paired)
        pd.DataFrame(matches,columns=['radius_m','prediction_index','tree_id','distance_m']).to_parquet(dest/'matches.parquet',index=False)
        metrics=dict(run_id=experiment,cohort=cohort,city=cfg.inventory.city,dataset=cfg.dataset,
            split='validation',chips=len(rows),ground_truth_trees=len(truth),candidate_predictions=len(p),
            predictions_above_threshold=int(p.above_threshold.sum()),confidence_threshold=.35,
            max_detections_per_chip=model.detections_per_img,metrics_by_match_radius_m=by_radius,
            source_raster=reference['source_raster'],detector='DeepForest NEON RetinaNet',
            attributes_available=[],checkpoint=str(weights))
        metadata={**reference,'config':cfg.model_dump(mode='json'),'run_id':experiment,
            'created_at':provenance['created_at'],'checkpoint':str(weights),'baseline':provenance}
        (dest/'evaluation-metadata.json').write_text(json.dumps(metadata,indent=2))
        (dest/'inference-coverage.json').write_text(json.dumps(coverage,indent=2))
        (dest/'metrics.json').write_text(json.dumps(metrics,indent=2))
        old=pd.read_parquet(source/'predictions.parquet')
        for name,predictions in ((experiment,p),(prepared_run,old)):
            for threshold in (.05,.1,.2,.3,.35,.4,.5,.6,.7,.8,.9):
                for policy in ('2.0','4.0','crown-half-radius-2to4m'):
                    scored,_=score_at(predictions,truth,scale,threshold,policy)
                    curves.append(dict(city=city,run=name,threshold=threshold,policy=policy,**scored))
        print(city,'complete',flush=True)
    (output/'baseline.json').write_text(json.dumps(provenance,indent=2))
    (output/'goal-curves.json').write_text(json.dumps(curves,indent=2))
    (output/'COMPLETE').write_text('Inference-only DeepForest baseline; both frozen validation cohorts complete. Test sealed.')


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--experiment',required=True)
    p.add_argument('--prepared-run',required=True);p.add_argument('configs',nargs='*')
    a=p.parse_args()
    for name in (a.experiment,a.prepared_run):
        if not name or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-' for c in name):
            raise ValueError('Invalid run name')
    run(Path(os.environ['TREE_ML_DATA_ROOT']),a.experiment,a.prepared_run)
