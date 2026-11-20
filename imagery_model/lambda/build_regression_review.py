"""Build a read-only, tree-linked packet from existing paired diagnostics."""
import hashlib
import json
from pathlib import Path
from urllib.parse import urlencode

import numpy as np
import pandas as pd
from PIL import Image, ImageDraw

from urban_tree_ml.crown_center_score import crown_matches, tolerance


ROOT = Path('C:/Users/ethan/coding_projects/sf_tree_reporting/imagery_model/artifacts')
WORK = Path(__file__).resolve().parents[1]
RUN = 'sf-boston-nyc-naip-swin-30cm-v16-curated-finetune'
PARENT = 'sf-boston-nyc-naip-swin-30cm-v15-curated-finetune'
PRIMARY = ROOT/'benchmarks/v15-vs-v16-frozen-validation-confidence-sweep/runs'/RUN/'evaluation'
OUT = WORK/'reports/v16-regression-review'
CITIES = {'ussfo': ('ussfo-2022-mosaic', 'validation'),
          'usbos': ('usbos-2023-external', 'validation-usbos')}


def main():
    OUT.mkdir(exist_ok=True)
    pairs = pd.read_parquet(ROOT/'benchmarks/v16-taxonomy-loss-cpu-sample/paired-targets.parquet')
    predictions = pd.read_parquet(ROOT/'benchmarks/v16-trained-detection-cpu-sample/predictions.parquet')
    cases, sources, contexts = [], {}, {}
    for city, (folder, cohort) in CITIES.items():
        directory = ROOT/'qa/registration'/folder
        manifest_path = directory/'manifest.json'
        state_path = directory/'reviews.json'
        for path in (manifest_path, state_path):
            sources[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
        manifest = json.loads(manifest_path.read_text())
        state = json.loads(state_path.read_text())
        truth = pd.read_parquet(PRIMARY/cohort/'ground-truth.parquet')
        groupdir = ROOT/'benchmarks/v15-vs-v16-single-tree-occupancy/chips'/(city+'-groups')
        chips = pd.read_parquet(groupdir/'chips.parquet').set_index('chip_id')
        contexts[city] = dict(truth=truth, scenes={s.get('validation_chip_id'):s for s in manifest['scenes']},
            samples={(s['scene_id'],s['tree_id']):s for s in manifest['samples']}, state=state,
            chips=chips, groupdir=groupdir)

    def add(city, truth, kind, details):
        ctx = contexts[city]
        scene = ctx['scenes'].get(truth.chip_id)
        if scene is None:
            return
        sample = ctx['samples'].get((scene['scene_id'], truth.tree_id))
        if sample is None:
            return
        case_id = f'case-{len(cases)+1:02}'
        link = 'http://127.0.0.1:8765/registration?' + urlencode(dict(
            city=city, scene=scene['scene_id'], fullscreen=1, run=PARENT,
            threshold='.13', radius='2.0'))
        tree_link = 'https://www.google.com/maps/@?' + urlencode(dict(
            api=1, map_action='pano', viewpoint=f'{truth.latitude},{truth.longitude}'))
        stored = ctx['state'].get('tree_reviews', {}).get(truth.tree_id, {})
        path = ctx['groupdir']/ctx['chips'].loc[truth.chip_id, 'path']
        with np.load(path) as z:
            rgb = np.clip(z['image'][:3].transpose(1,2,0)*255,0,255).astype(np.uint8)
        tile = Image.fromarray(rgb)
        x, y = int(truth.output_x)*2, int(truth.output_y)*2
        crop = tile.crop((x-64,y-64,x+64,y+64)).resize((384,384))
        full = tile.copy()
        for im,cx,cy in [(full,x,y),(crop,192,192)]:
            draw=ImageDraw.Draw(im)
            draw.ellipse((cx-10,cy-10,cx+10,cy+10),outline='#ffff00',width=2)
            for line in [(cx-17,cy,cx-7,cy),(cx+7,cy,cx+17,cy),
                         (cx,cy-17,cx,cy-7),(cx,cy+7,cx,cy+17)]:
                draw.line(line,fill='#ffff00',width=2)
        if kind == 'detection':
            draw = ImageDraw.Draw(crop)
            for model,color,radius in [('parent','#00ff80',6),('best','#44bbff',10),('final','#ff66cc',14)]:
                near=details['nearest_above_threshold'][model]
                if near is None:continue
                px=192+(near['x']*2-x)*3;py=192+(near['y']*2-y)*3
                draw.ellipse((px-radius,py-radius,px+radius,py+radius),outline=color,width=2)
        image=Image.new('RGB',(912,550),'#18202b')
        image.paste(full,(0,30));image.paste(crop,(528,30))
        draw=ImageDraw.Draw(image)
        draw.text((8,8),f'{case_id}: {city} / {truth.chip_id} / {truth.tree_id}',fill='white')
        draw.text((532,422),'Yellow marker: frozen labeled center',fill='white')
        draw.text((532,444),'Right crop: 38.4 m across; not model crown',fill='white')
        if kind == 'detection':
            draw.text((532,468),'Nearest peaks: v15 green / best blue / final pink',fill='white')
        image.save(OUT/(case_id+'.png'))
        cases.append(dict(id=case_id,city=city,kind=kind,chip=truth.chip_id,tree_id=truth.tree_id,
            scene=scene['scene_id'],sample_id=sample['sample_id'],x=int(truth.output_x),y=int(truth.output_y),
            source_x=x,source_y=y,latitude=float(truth.latitude),longitude=float(truth.longitude),
            tile_link=link,tree_location_link=tree_link,manifest_species=sample.get('species'),
            saved_review=stored,details=details))

    wanted=[('usbos','bos-29022','species'),('ussfo','sf-217437','species'),
            ('usbos','cam-150580','genus'),('ussfo','sf-189832','species'),
            ('usbos','bos-29478','species'),('ussfo','sf-205782','species'),
            ('usbos','cam-3680','species'),('ussfo','sf-225514','species')]
    for city,tree,head in wanted:
        truth=contexts[city]['truth']
        tr=truth[truth.tree_id.eq(tree)]
        assert len(tr)==1,(city,tree)
        tr=tr.iloc[0]
        p=pairs[pairs.city.eq(city)&pairs.chip_id.eq(tr.chip_id)&pairs.x.eq(tr.output_x)
                &pairs.y.eq(tr.output_y)&pairs['head'].eq(head)].set_index('candidate')
        assert len(p)==2 and p.loc['best','correct_parent'] and not p.loc['best','correct_candidate']
        best=p.loc['best']; final=p.loc['final']
        add(city,tr,'taxonomy',dict(head=head,target=best.target_name,
            parent=best.predicted_name_parent,best=best.predicted_name_candidate,final=final.predicted_name_candidate,
            true_probability_parent=float(best.probability_true_parent),
            true_probability_best=float(best.probability_true_candidate),
            true_probability_final=float(final.probability_true_candidate),
            loss_delta_best=float(best.delta),loss_delta_final=float(final.delta)))

    # Find true matched -> missed transitions against all truths on each chip.
    # Require no candidate peak above .13 anywhere in the target's tolerance,
    # avoiding one-to-one assignment changes masquerading as disappearance.
    for city,ctx in contexts.items():
        candidates=[]
        pp=predictions[predictions.city.eq(city)]
        for chip,raw in pp.groupby('chip_id',sort=False):
            if chip not in ctx['scenes']:continue
            truth=ctx['truth'][ctx['truth'].chip_id.eq(chip)].reset_index(drop=True)
            models={model:raw[raw.model.eq(model)] for model in ('parent','best','final')}
            matches={model:{ti:pi for pi,ti,_ in crown_matches(p[p.score.ge(.13)],truth,.6)} for model,p in models.items()}
            for ti,pi in matches['parent'].items():
                if ti in matches['best']:continue
                tr=truth.loc[ti];radius=tolerance(tr.crown_radius_m)/.6
                nearby={}
                nearest={}
                for model,p in models.items():
                    near=p[np.hypot(p.output_x-tr.output_x,p.output_y-tr.output_y)<=radius]
                    nearby[model]=float(near.score.max()) if len(near) else None
                    above=p[p.score.ge(.13)]
                    if len(above):
                        distances=np.hypot(above.output_x-tr.output_x,above.output_y-tr.output_y)*.6
                        closest=above.loc[distances.idxmin()]
                        nearest[model]=dict(x=int(closest.output_x),y=int(closest.output_y),
                            distance_m=float(distances.min()),score=float(closest.score))
                    else:nearest[model]=None
                if nearby['best'] is not None and nearby['best']>=.13:continue
                parent_score=float(models['parent'].loc[pi,'score'])
                candidates.append((parent_score,tr,dict(threshold=.13,parent_match_score=parent_score,
                    nearby_peak_max=nearby,nearest_above_threshold=nearest,
                    final_detected=ti in matches['final'],tolerance_m=radius*.6)))
        for _,tr,details in sorted(candidates,key=lambda c:-c[0])[:2]:
            add(city,tr,'detection',details)

    (OUT/'cases.json').write_text(json.dumps(cases,indent=2),encoding='utf-8')
    (OUT/'live-source-hashes.json').write_text(json.dumps(sources,indent=2))
    lines=['# Specific v15 to rejected-v16 regressions for curation review','',
        'These are paired CPU float32 diagnostics on identical frozen v16 validation images and labels. Parent is v15; best-trained is v16 step848; final is v16 step1132. The selected v16 model is v15, so ordinary v15/v16 UI overlays will look identical. The rejected candidates are not registered as reviewer models. No server restart, annotation mutation, model switch, or training was performed.','',
        'Taxonomy rows are predictions at the labeled center, even if no detection exists there. Correct/incorrect means agreement with the frozen label, not independently verified botanical truth. Detection rows use the same pre-tie-fix decoder for all models, confidence .13 and crown-aware matching. They require the best candidate to have no above-threshold peak within the matching tolerance, rather than merely losing a match to another tree.','',
        'Tile links open existing curation scenes with v15 overlays. They do not auto-select a tree: use the tree ID and yellow-marked image. Tree-location links open Street View near the frozen coordinates, not a guarantee that a panorama exists or matches the imagery year. Coordinates in this report are output-grid pixels (0-255); full source pixels are twice these. Current live edits may differ from the frozen image target.','',
        'For each case check: Is there a real tree at the marked center? Is the marker on the correct crown? Is the species identification supported, or only genus/unknown? Are inventory names, neighboring trees, occlusion or imagery dates misleading the label? Do not change a label solely to agree with either model.','']
    for case in cases:
        d=case['details'];stem=case['id']
        lines += [f"## {stem}: {case['city']} / {case['tree_id']} ({case['kind']})",'',
            f"[Open curation tile]({case['tile_link']}) · [Tree location / Street View]({case['tree_location_link']})",'',
            f"Tile `{case['chip']}`, scene `{case['scene']}`, sample `{case['sample_id']}`; frozen center **({case['x']}, {case['y']})**.",'']
        if case['kind']=='taxonomy':
            lines += [f"Frozen {d['head']} label: **{d['target']}**.",'',
                '| Model | Prediction at labeled center | Probability assigned to frozen label |',
                '| --- | --- | --- |',
                *[f"| {label} | {d[key]} | {d['true_probability_'+key]:.1%} |" for label,key in [('v15 parent','parent'),('v16 best-trained, step848','best'),('v16 final, step1132','final')]],'',
                f"Cross-entropy increase: best {d['loss_delta_best']:+.3f}; final {d['loss_delta_final']:+.3f}.",'']
        else:
            lines += [f"Parent matched this tree at confidence **{d['parent_match_score']:.3f}**. Best-trained misses it at .13; matching tolerance {d['tolerance_m']:.2f} m. Final detected: **{d['final_detected']}**.",'',
                'Highest decoded peak within tolerance: '+', '.join(f"{k} {v:.3f}" if v is not None else f"{k}: no cached peak" for k,v in d['nearby_peak_max'].items())+'.','']
            lines += ['Nearest peak above .13 (may belong to a neighboring tree): '+', '.join(
                f"{k} {v['distance_m']:.2f} m away (score {v['score']:.3f})" if v else f"{k}: none"
                for k,v in d['nearest_above_threshold'].items())+'.','']
        review=case['saved_review']
        lines += [f"Read-only current manifest species: `{case['manifest_species']}`. Saved review: `{json.dumps(review,sort_keys=True)}`.",'',
            f"![Marked frozen tile and tree crop]({(OUT/(stem+'.png')).as_posix()})",'']
    (OUT/'README.md').write_text('\n'.join(lines),encoding='utf-8')
    print(json.dumps([{k:c[k] for k in ['id','city','tree_id','kind','tile_link','details']} for c in cases],indent=2))


if __name__=='__main__':
    main()
