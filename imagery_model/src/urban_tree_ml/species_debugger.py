"""Paired species review over saved development predictions; no inference."""
import io
import json
import math
from types import SimpleNamespace
from urllib.parse import urlencode

import polars as pl
from urban_tree_ml.review_tables import above_threshold, by_chip, scan_predictions
from PIL import Image

from urban_tree_ml.species_corrections import normalize_species_override

DIAG_RUN = 'sf-boston-nyc-naip-swin-30cm-v16-curated-finetune'
DIAG_PREFIX = 'diagnostic-v16:'


def options(catalog, root, city, split):
    if split not in ('train', 'validation'):
        raise ValueError('Choose training or validation; test is sealed')
    records = [r for r in catalog.records() if r['city'].lower() == city
               and r['metrics'].get('split') == split and not r['metrics'].get('prediction_only')]
    result = [dict(id=r['run_id'], label=r['training_run_id'], diagnostic=False)
              for r in sorted(records, key=lambda r: r['created_at'], reverse=True)]
    diagnostic = root/'benchmarks/v16-trained-detection-cpu-sample'
    taxonomy = root/'benchmarks/v16-taxonomy-loss-cpu-sample'
    if split == 'validation' and (diagnostic/'COMPLETE').exists() and (taxonomy/'COMPLETE').exists() and any(
            r['training_run_id'] == DIAG_RUN for r in records):
        result = [dict(id=DIAG_PREFIX+key, label=label+' · CPU sample', diagnostic=True) for key,label in [
            ('parent','v15 parent'),('best','v16 rejected step848'),('final','v16 rejected final step1132')]] + result
    return result


def scope_options(catalog, contexts, city, split):
    """All-city selections identify a model family, then resolve each city cohort."""
    owners=contexts if city=='all' else {city:contexts[city]}
    families={}
    for code,owner in owners.items():
        for item in options(catalog,owner.config.paths.root,code,split):
            key=item['id'] if city!='all' or item['diagnostic'] else item['label']
            entry=families.setdefault(key,dict(id=key,label=item['label'],diagnostic=item['diagnostic'],city_runs={}))
            entry['city_runs'].setdefault(code,item['id'])
    return list(families.values())


def compare_scope(catalog,contexts,city,split,a,b,threshold,policy,basis,owns,progress):
    choices={o['id']:o for o in scope_options(catalog,contexts,city,split)}
    if a not in choices or b not in choices:raise ValueError('Choose two available models')
    requested=list(contexts) if city=='all' else [city]
    rows=[];included=[];excluded=[];species=set();chips=0;first_result=None
    for code in requested:
        selected=[choices[k]['city_runs'].get(code) for k in (a,b)]
        if not all(selected):
            excluded.append(dict(city=code,reason='Both models do not have this development cohort'))
            continue
        owner=contexts[code]
        progress(f'{code.upper()} · reading saved predictions')
        first=load_source(catalog,owner.config.paths.root,code,split,selected[0])
        second=load_source(catalog,owner.config.paths.root,code,split,selected[1])
        if not all(owns(owner,s) for s in (first,second)):
            raise ValueError(f'{code}: models must share this curation imagery')
        if not set(first.chip_ids)&set(second.chip_ids):
            excluded.append(dict(city=code,reason='Models have no shared development tiles'))
            continue
        result=paired_rows(first,second,threshold,policy,basis,lambda message:progress(code.upper()+' · '+message))
        if first_result is None:first_result={k:v for k,v in result.items() if k not in ('rows','ranking','species')}
        for row in result['rows']:
            rows.append({**row,'id':code+':'+row['id'],'city':code,'city_label':owner.label,
                **{k:result[k] for k in ('image_run','scale','output_pixels')}})
        included.append(dict(city=code,label=owner.label,chips=result['chips']))
        chips+=result['chips'];species.update(result['species'])
        del first,second,result
    if first_result is None:raise ValueError('Models have no shared development tiles in the selected cities')
    return {**first_result,'rows':rows,'chips':chips,'species':sorted(species),
            'ranking':species_ranking(rows,basis),'cities':included,'excluded_cities':excluded}


def load_source(catalog, root, city, split, selected):
    if selected not in {o['id'] for o in options(catalog,root,city,split)}:
        raise ValueError('Model is not available for this city/development split')
    if not selected.startswith(DIAG_PREFIX):
        bundle = worker_bundle(catalog, selected)
        return SimpleNamespace(config=bundle.config, raster_path=bundle.raster_path,
            taxonomy=bundle.taxonomy, predictions=bundle.predictions, ground_truth=bundle.ground_truth,
            chip_ids=bundle.chip_ids, image_run=selected, centers=None)
    model = selected.removeprefix(DIAG_PREFIX)
    record = next(r for r in catalog.records() if r['training_run_id']==DIAG_RUN
                  and r['city'].lower()==city and r['metrics'].get('split')=='validation')
    bundle = worker_bundle(catalog, record['run_id'])
    directory=root/'benchmarks/v16-trained-detection-cpu-sample'
    windows = pl.scan_parquet(directory/'windows.parquet')
    ids = set(windows.filter((pl.col('city') == city) & pl.col('primary')).select('chip_id').collect().to_series())
    selected_rows = (pl.col('model') == model) & (pl.col('city') == city)
    p = scan_predictions(directory/'predictions.parquet').filter(selected_rows & pl.col('chip_id').is_in(ids)).collect()
    t = pl.scan_parquet(root/'benchmarks/v15-vs-v16-frozen-validation-confidence-sweep/runs'/DIAG_RUN/
                        'evaluation'/record['cohort']/'ground-truth.parquet').filter(pl.col('chip_id').is_in(ids)).collect()
    centers = (pl.scan_parquet(root/'benchmarks/v16-taxonomy-loss-cpu-sample/targets.parquet')
               .filter(selected_rows & (pl.col('head') == 'species')).collect())
    return SimpleNamespace(config=bundle.config,raster_path=bundle.raster_path,taxonomy=bundle.taxonomy,
        predictions=p,ground_truth=t,chip_ids=sorted(ids),
        image_run=record['run_id'],centers=centers)


def worker_bundle(catalog, selected):
    """Keep background matching outside the interactive model-cache lock."""
    from urban_tree_ml.model_debug import ModelDebugBundle
    record = next(r for r in catalog.records() if r['run_id'] == selected)
    return ModelDebugBundle(record['bundle_config'], record['evaluation_dir'], record['raster_path'],
                            evaluation_id=selected, artifact_root=catalog.config.paths.root)


def class_name(taxonomy, value):
    return taxonomy['species'][int(value)] if value is not None and math.isfinite(value) and 0<=value<len(taxonomy['species']) else None


def _match_tile(entries, truth, scale, policy):
    targets = []
    for row in truth.iter_rows(named=True):
        radius = 4.0 if policy == 'fixed-4m' else 2.0
        crown = row.get('crown_radius_m')
        if policy.startswith('crown') and crown is not None and math.isfinite(crown):
            radius = min(4.0, max(2.0, crown / 2))
        targets.append((row['output_x'], row['output_y'], radius / scale))
    used = set()
    pairs = []
    for i in sorted(range(len(entries)), key=lambda i: -entries[i]['score']):
        best, distance = None, math.inf
        for j, (x, y, radius) in enumerate(targets):
            if j in used:
                continue
            d = math.hypot(entries[i]['x'] - x, entries[i]['y'] - y)
            if d <= radius and d < distance:
                best, distance = j, d
        if best is not None:
            used.add(best)
            pairs.append((i, best))
    return pairs


def paired_rows(a,b,threshold,policy,basis,progress=lambda _:None):
    if not math.isfinite(threshold) or not 0<=threshold<=1:
        raise ValueError('Confidence must be between zero and one')
    if policy not in ('crown-half-radius-2-4m','fixed-2m','fixed-4m'):
        raise ValueError('Unknown matching policy')
    if basis not in ('matched','center') or (basis=='center' and (a.centers is None or b.centers is None)):
        raise ValueError('Label-center classification requires two diagnostic checkpoints')
    if (a.raster_path.resolve()!=b.raster_path.resolve() or
        a.config.imagery.chip_pixels!=b.config.imagery.chip_pixels or
        a.config.imagery.resolution_m!=b.config.imagery.resolution_m or
        a.config.targets.output_stride!=b.config.targets.output_stride):
        raise ValueError('Choose models with identical imagery and output geometry')
    common=sorted(set(a.chip_ids)&set(b.chip_ids))
    if not common:raise ValueError('Models have no common development tiles')
    scale=b.config.imagery.resolution_m*b.config.targets.output_stride
    truths = by_chip(b.ground_truth)
    groups = [by_chip(s.predictions.filter(above_threshold(threshold))) for s in (a, b)]
    centers = [{} if s.centers is None else {
        (r['chip_id'], int(r['x']), int(r['y'])): dict(species=r['predicted_name'],
            confidence=float(r['confidence']), true_probability=float(r['probability_true']))
        for r in s.centers.iter_rows(named=True)} for s in (a, b)]
    rows=[]
    for number,chip in enumerate(common):
        if number%50==0:progress(f'Matching tile {number+1} of {len(common)}')
        t = truths.get(chip, b.ground_truth.head(0))
        matched, unmatched = [], []
        for source, group in zip((a, b), groups):
            p = group.get(chip, source.predictions.head(0))
            entries = []
            for q in p.iter_rows(named=True):
                conf = q.get('species_confidence')
                entries.append(dict(x=float(q['output_x']), y=float(q['output_y']), score=float(q['score']),
                               species=class_name(source.taxonomy, q.get('species_id')),
                               confidence=float(conf) if conf is not None and math.isfinite(conf) else None))
            pairs = _match_tile(entries, t, scale, policy)
            matched.append({ti: entries[pi] for pi, ti in pairs})
            used = {pi for pi, _ in pairs}
            unmatched.append([(i, entry) for i, entry in enumerate(entries) if i not in used])
        for i, tr in enumerate(t.iter_rows(named=True)):
            truth_name = class_name(b.taxonomy, tr['species_id'])
            key = (chip, int(tr['output_x']), int(tr['output_y']))
            ca,cb=centers[0].get(key),centers[1].get(key)
            pa,pb=matched[0].get(i),matched[1].get(i)
            va,vb=(ca,cb) if basis=='center' else (pa,pb)
            correct_a=truth_name is not None and va is not None and va['species']==truth_name
            correct_b=truth_name is not None and vb is not None and vb['species']==truth_name
            status=('regressed' if correct_a and not correct_b else 'improved' if correct_b and not correct_a
                    else 'both-correct' if correct_a and correct_b else 'both-wrong' if truth_name else 'unknown-label')
            if basis == 'center' and truth_name and (ca is None or cb is None):
                status = 'unavailable'
            rows.append(dict(id=f"{chip}:tree:{tr['tree_id']}",chip=chip,tree_id=str(tr['tree_id']),
                x=float(tr['output_x']),y=float(tr['output_y']),truth=truth_name,a=pa,b=pb,
                center_a=ca,center_b=cb,status=status,kind='tree'))
        if basis=='matched':
            unmatched_rows={}
            for model,entries in zip(('a','b'),unmatched):
                for pi,entry in entries:
                    location=(entry['x'],entry['y'])
                    row=unmatched_rows.setdefault(location,dict(id=f'{chip}:prediction:{location}',chip=chip,
                        tree_id=None,x=entry['x'],y=entry['y'],truth=None,a=None,b=None,
                        center_a=None,center_b=None,status='unmatched',kind='prediction'))
                    row[model]=entry
            rows.extend(unmatched_rows.values())
    return dict(rows=rows,chips=len(common),image_run=b.image_run,scale=scale,basis=basis,threshold=threshold,
        ranking=species_ranking(rows,basis),
        output_pixels=b.config.imagery.chip_pixels/b.config.targets.output_stride,
        species=sorted({name for r in rows for name in [r['truth'],
            *((r[k] or {}).get('species') for k in ('a','b','center_a','center_b'))] if name}))


def taxonomy_outcomes(row,basis):
    a,b=[(row[k] or {}).get('species') for k in (('center_a','center_b') if basis=='center' else ('a','b'))]
    truth=row['truth']
    return a,b,bool(truth and b and b!=truth),bool(truth and a==truth and b and b!=truth)


def species_ranking(rows,basis):
    """Classification errors only; absent detections/samples are not wrong species."""
    grouped={}
    for row in rows:
        name=row['truth']
        if row['kind']!='tree' or not name or name.startswith('__'):
            continue
        entry=grouped.setdefault(name,dict(species=name,labeled=0,a_classified=0,a_wrong=0,
            b_classified=0,b_wrong=0,regressions=0))
        a,b,wrong,regressed=taxonomy_outcomes(row,basis)
        entry['labeled']+=1
        entry['a_classified']+=int(bool(a));entry['a_wrong']+=int(bool(a and a!=name))
        entry['b_classified']+=int(bool(b));entry['b_wrong']+=int(wrong)
        entry['regressions']+=int(regressed)
    return sorted((r for r in grouped.values() if r['b_wrong']),
                  key=lambda r:(-r['regressions'],-r['b_wrong'],r['species']))


def page(result,species,status,scope,page_number,auto_pick=False):
    if status not in ('all','regressed','improved','both-correct','both-wrong','unknown-label','unmatched','unavailable','b-mismatch','taxonomy-regressed'):
        raise ValueError('Invalid result filter')
    if scope not in ('any','truth','predicted'):raise ValueError('Invalid species scope')
    if auto_pick and not species and result['ranking']:
        suggested=result['ranking'][0]
        species,scope=suggested['species'],'truth'
        status='taxonomy-regressed' if suggested['regressions'] else 'b-mismatch'
    def keep(row):
        predicted=[(row[k] or {}).get('species') for k in (('center_a','center_b') if result['basis']=='center' else ('a','b'))]
        def matches(name):
            return bool(name and (name.casefold()==species.casefold() or name.split()[0].casefold()==species.casefold()))
        _,_,wrong,regressed=taxonomy_outcomes(row,result['basis'])
        outcome=(wrong if status=='b-mismatch' else regressed if status=='taxonomy-regressed'
                 else status=='all' or row['status']==status)
        return outcome and (not species or
            (scope!='predicted' and matches(row['truth'])) or (scope!='truth' and any(matches(n) for n in predicted)))
    selected=[r for r in result['rows'] if keep(r)]
    order={'regressed':0,'both-wrong':1,'unmatched':2,'unknown-label':3,'improved':4,'both-correct':5,'unavailable':6}
    selected.sort(key=lambda r:(order[r['status']],r.get('city',''),r['chip'],r['id']))
    page_number=max(0,min(int(page_number),max(0,(len(selected)-1)//24)))
    return {k:v for k,v in result.items() if k!='rows'} | dict(rows=selected[page_number*24:(page_number+1)*24],
        page=page_number,total=len(selected),pages=max(1,math.ceil(len(selected)/24)),
        selected_species=species,selected_status=status,selected_scope=scope)


def label_target(manifest,state,chip,tree_id):
    scene=next((s for s in manifest['scenes'] if s.get('validation_chip_id')==chip
                and 'test' not in s.get('splits',[])),None)
    if scene is None:raise ValueError('Open this tile in curation once to initialize its editable scene')
    sample=next((s for s in manifest['samples'] if s['sample_id'] in scene['sample_ids']
                 and str(s['tree_id'])==str(tree_id) and s.get('split')!='test'),None)
    region=next((r for r in state['mask_regions'] if r.get('mode')=='confirmed-tree'
                 and 'manual-'+r['region_id']==tree_id and not r.get('tree_id')),None)
    if sample and region is None:
        review=state['reviews'].get(sample['sample_id'],{})
        return dict(scene=scene['scene_id'],sample_id=sample['sample_id'],tree_id=tree_id,
            original_species=sample.get('species') or '',current_species=(review['species_override']
                if review.get('species_override') is not None else sample.get('species') or ''),
            override=review.get('species_override') is not None,review=review,base_revision=state['state_revision'])
    if region:
        from urban_tree_ml.review_gallery import intersects,scoped_manifest
        if not intersects(region,scoped_manifest(manifest,scene['scene_id'])):raise ValueError('Tree is outside this scene')
        return dict(scene=scene['scene_id'],tree_id=tree_id,region=region,current_species=region.get('species',''),
            original_species=None,override=False,base_revision=state['state_revision'])
    raise ValueError('Tree is not an editable member of this tile')


def label_patch(target,payload):
    if payload.get('base_revision')!=target['base_revision']:
        from urban_tree_ml.feedback import ReviewStateConflictError
        raise ReviewStateConflictError('Annotations changed; reload this tree before saving')
    name=normalize_species_override(payload.get('species'))
    if 'region' in target:
        if payload.get('reset'):raise ValueError('Added trees have no inventory label to restore')
        return dict(base_revision=target['base_revision'],mask_regions=[{**target['region'],'species':name}])
    review=dict(target['review'])
    if payload.get('reset'):review['species_override']=None
    else:review['species_override']=name
    return dict(base_revision=target['base_revision'],reviews={target['sample_id']:review})


def crop_png(bundle,chip,x,y,span):
    values=(float(x),float(y),float(span))
    if not all(math.isfinite(v) for v in values) or not 12<=values[2]<=90:
        raise ValueError('Choose a finite crop between 12 and 90 meters')
    x,y,span=values
    pixels=bundle.config.imagery.chip_pixels/bundle.config.targets.output_stride
    if not 0<=x<pixels or not 0<=y<pixels:raise ValueError('Center is outside the tile')
    if chip not in bundle.chip_ids:
        raise ValueError('Unknown development tile')
    import rasterio
    from rasterio.windows import Window
    from urban_tree_ml.chip_geometry import chip_offsets
    from urban_tree_ml.quality import _rgb_preview
    stride=bundle.config.targets.output_stride
    half=span/bundle.config.imagery.resolution_m/2
    row,col=chip_offsets(chip,bundle.config.imagery.chip_pixels)
    # Read a small window of the same raster, including adjacent tile context.
    # Keep a bounded output and GDAL cache even for large COG/VRT mosaics.
    window=Window(col+x*stride-half,row+y*stride-half,2*half,2*half)
    with rasterio.Env(GDAL_CACHEMAX=32*1024*1024), rasterio.open(bundle.raster_path) as source:
        raw=source.read(bundle.config.imagery.bands[:3],window=window,boundless=True,
                        fill_value=0,out_shape=(3,320,320))
    crop=Image.fromarray(_rgb_preview(raw,bundle.config.imagery.input_scale))
    out=io.BytesIO();crop.save(out,format='PNG')
    return out.getvalue()
