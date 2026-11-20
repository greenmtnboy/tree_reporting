"""Read-only audit of registered imagery, catalogs, model ownership and UI routes."""
import argparse
import hashlib
import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen


def audit(studio_path, output, family, live):
    from urban_tree_ml.config import load_config, load_studio_config
    from urban_tree_ml.qa_server import _checked_review_context, _context_matches_bundle
    from urban_tree_ml.temporal_studio import load_contexts, record_acquisition
    from urban_tree_ml.model_debug import RunDebugCatalog
    from urban_tree_ml.catalog_imagery import matching_catalog_summary, matches_catalog_imagery
    from urban_tree_ml.queue_navigation import model_identity, review_split_ids
    from urban_tree_ml.review_assignments import geographic_assignments
    import polars as pl

    studio = load_studio_config(studio_path)
    contexts = {c.city: _checked_review_context(c.city,c.label,load_config(c.config_path),
                c.raster,c.review_dir,c.evaluation_dir) for c in studio.cities}
    extra, registry = load_contexts(contexts,_checked_review_context,load_config)
    owners = {c.acquisition_id or c.city:c for c in [*contexts.values(),*extra.values()]}
    default = contexts[studio.default_city]
    root = default.config.paths.root
    failures, warnings, workspaces, evaluations = [], [], [], []
    def check(ok, message):
        if not ok: failures.append(message)
    summaries=[]
    for path in (root/'chips').glob('*/summary.json'):
        try: summaries.append((path,json.loads(path.read_text(encoding='utf-8-sig'))))
        except (ValueError,OSError) as error: warnings.append(f'{path}: {error}')
    for key,c in owners.items():
        manifest=json.loads((c.directory/'manifest.json').read_text(encoding='utf-8'))
        meta=manifest['metadata']; scenes=manifest['scenes']
        check(matches_catalog_imagery(root,meta.get('source_raster'),c.raster),f'{key}: manifest raster mismatch')
        ids=[s['scene_id'] for s in scenes]
        check(len(ids)==len(set(ids)),f'{key}: duplicate scene IDs')
        check(not meta.get('test_labels_included'),f'{key}: test labels in development workspace')
        if meta.get('acquisition_id'):
            check(meta['acquisition_id']==key,f'{key}: manifest identifies another year')
            check(meta.get('split_policy_sha256')==registry[key]['split_policy_sha256'],f'{key}: manifest split policy differs')
            check(meta.get('curation_imagery_id')==hashlib.sha256(c.raster.read_bytes()).hexdigest(),f'{key}: registered temporal raster changed')
        for scene in scenes:
            image=(c.directory/scene['image']).resolve()
            check(image.is_relative_to(c.directory.resolve()) and image.is_file(),f"{key}/{scene['scene_id']}: missing or external scene image")
            check('test' not in scene.get('splits',[]),f"{key}/{scene['scene_id']}: sealed scene in review workspace")
        candidates=[p.parent.name for p,s in summaries if matching_catalog_summary(root,s,c.raster)]
        assignments=geographic_assignments(c)
        if c.acquisition_id and registry[key].get('reference_chips'):
            reference=Path(registry[key]['reference_chips'])
            check(reference.resolve().is_relative_to((root/'chips').resolve()),f'{key}: external reference catalog')
            check(hashlib.sha256(reference.read_bytes()).hexdigest()==meta['reference_split_manifest_sha256'],
                  f'{key}: reference split manifest changed')
            frame=pl.read_parquet(reference,columns=['chip_id','split'])
            planned=dict(zip(frame['chip_id'],frame['split']))
            check(len(planned)==len(frame) and set(planned.values())<={'train','validation'},
                  f'{key}: invalid development split catalog')
            for scene in scenes:
                chip=scene.get('validation_chip_id')
                check(chip in planned and scene.get('splits')==[planned[chip]],f'{key}/{chip}: scene split differs')
        elif c.city=='usffx':
            from urban_tree_ml.fairfax_curation import development_plan, RUN
            plan=development_plan(root/'run-inputs'/RUN,root/'inventory/usffx/inventory.parquet')
            planned={f"r{w['row']//512:06d}_c{w['col']//512:06d}" for w in plan['windows']}
            check({s['validation_chip_id'] for s in scenes if s.get('validation_chip_id')}<=planned,
                  f'{key}: registration scene outside the approved Fairfax development plan')
        workspaces.append(dict(id=key,city=c.city,raster=str(c.raster),review_dir=str(c.directory),
            scenes=len(scenes),scene_images_checked=len(scenes),scene_splits=dict(Counter('+'.join(s.get('splits',[])) for s in scenes)),
            matching_catalogs=candidates,assignments=len(assignments)))
        print('Workspace',key,len(scenes),'scenes',len(candidates),'matching catalogs',flush=True)
    for a,ca in owners.items():
        for b,cb in owners.items():
            if a!=b:
                check(not matches_catalog_imagery(root,ca.raster,cb.raster),f'{a}/{b}: separate workspaces alias imagery')
                check(ca.directory.resolve()!=cb.directory.resolve(),f'{a}/{b}: shared annotation directory')
    queue_checks=[]
    queue_path=root/'qa/geographic-review-assignments.json'
    for queue in (json.loads(queue_path.read_text())['assignments'] if queue_path.exists() else []):
        check(queue['split'] in ('train','validation'),f"{queue['queue']}: sealed assignment split")
        source=root/'benchmarks'/queue['snapshot']/'validation-targets.csv'
        check(source.is_file() and hashlib.sha256(source.read_bytes()).hexdigest()==queue['source_sha256'],
              f"{queue['queue']}: changed assignment source")
        for acquisition,scope in queue['acquisitions'].items():
            c=owners.get(acquisition)
            check(c is not None,f'{acquisition}: assignment has no registered owner')
            if c is None:continue
            check(c.city==queue['city'],f'{acquisition}: assignment belongs to another city')
            check(hashlib.sha256(c.raster.read_bytes()).hexdigest()==scope['raster_sha256'],f'{acquisition}: assignment raster changed')
            check(c.config.imagery.chip_pixels==scope['chip_pixels'],f'{acquisition}: assignment grid changed')
            check(registry[acquisition]['split_policy_sha256']==queue['split_policy_sha256'],f'{acquisition}: assignment split policy changed')
            count=len(geographic_assignments(c))
            check(count>0,f'{acquisition}: verified assignment disappeared')
            queue_checks.append(dict(queue=queue['queue'],acquisition=acquisition,assigned_chips=count))
    catalog=RunDebugCatalog(default.config,default.evaluation_dir,default.raster)
    records=catalog.records()
    for record in records:
        matching=[]; errors=[]
        for key,c in owners.items():
            try:
                if _context_matches_bundle(c,model_identity(record)): matching.append(key)
            except (OSError,ValueError) as error: errors.append(str(error))
        check(len(matching)<=1,f"{record['run_id']}: ambiguous owners {matching}")
        for key in matching:
            check(owners[key].city==record['city'].lower(),f"{record['run_id']}: city mismatch")
            if record_acquisition(record):
                check(key==record_acquisition(record),f"{record['run_id']}: year mismatch")
        current=record['training_run_id']==family
        check(record['metrics'].get('city',record['city']).lower()==record['city'].lower(),f"{record['run_id']}: metadata city disagreement")
        if current: check(len(matching)==1,f"{record['run_id']}: current model lacks unique imagery owner {errors}")
        coverage=record['evaluation_dir']/'inference-coverage.json'
        entry=dict(run=record['run_id'],owners=matching,current=current)
        if current and matching and coverage.exists():
            c=owners[matching[0]]; split=record['metrics']['split']
            saved=json.loads(coverage.read_text()); chips=[x['chip_id'] for x in saved['chips']]
            check(saved['complete'] and len(chips)==len(set(chips)),f"{record['run_id']}: incomplete/duplicate coverage")
            admitted=review_split_ids(c,record,split)
            check(set(chips)<=admitted,f"{record['run_id']}: inference outside declared split")
            manifest_path=root/'chips'/record['dataset']/'chips.parquet'
            if manifest_path.exists():
                frame=pl.read_parquet(manifest_path,columns=['chip_id','split'])
                sealed=set(frame.filter(pl.col('split')=='test')['chip_id'])
                check(not set(chips)&sealed,f"{record['run_id']}: test chips in development inference")
            entry.update(chips=len(chips),split=split)
        evaluations.append(entry)
    live_checks=[];live_grid=None
    if live:
        def get(path,params):
            with urlopen(live+path+'?'+urlencode(params),timeout=120) as response:
                return json.load(response)
        grid=get('/api/model/all-summary',dict(run=family+'::validation-usffx',city='all',split='all',acquisition='all'))
        rows=grid['chips'];keys=[r['key'] for r in rows]
        live_grid=dict(rows=len(rows),by_imagery_split=dict(Counter(
            (r.get('acquisition_id') or r['city'])+'/'+r['split'] for r in rows)))
        check(len(keys)==len(set(keys)),'Live grid has duplicate imagery-qualified keys')
        check(not any(r['city']=='usffx' and r['chip_id']=='r000025_c000039' for r in rows),'SF chip leaked into Fairfax')
        record_map={r['run_id']:r for r in records}
        groups={}
        allowed={}
        for row in rows:
            run=row.get('inference_run') or row.get('review_run')
            if run:
                record=record_map[run]
                check(record['city'].lower()==row['city'],f"{row['key']}: wrong row city")
                check(record_acquisition(record)==(row.get('acquisition_filter') or None),f"{row['key']}: wrong row year")
                check(row['split']==record['metrics']['split'],f"{row['key']}: wrong row split")
                owner=next((c for c in owners.values() if _context_matches_bundle(c,model_identity(record))),None) if run not in allowed else None
                if run not in allowed:
                    allowed[run]=review_split_ids(owner,record,row['split']) if owner else set()
                check(row['chip_id'] in allowed[run],f"{row['key']}: row outside cohort split manifest")
                groups.setdefault(run,row)
        for run,row in groups.items():
            query=dict(city=row['city'],run=run,chip=row['chip_id'],split=row['split'],compare_run='auto')
            if row.get('acquisition_filter'):query['acquisition']=row['acquisition_filter']
            member=get('/api/review-next',query)
            check(member['run']==run and member['chip']==row['chip_id'],f'{run}: live navigation mismatch')
            live_checks.append(dict(run=run,chip=row['chip_id'],inference_unavailable=member['inference_unavailable']))
        print('Live grid',len(rows),'rows;',len(groups),'cohort routes verified',flush=True)
    result=dict(at=datetime.now(timezone.utc).isoformat(),family=family,workspaces=workspaces,
                evaluations=evaluations,live_checks=live_checks,live_grid=live_grid,queues=queue_checks,failures=failures,warnings=warnings,
                annotation_writes=0)
    Path(output).write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print('FAILURES',json.dumps(failures),flush=True)
    return not failures


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--studio',default='configs/studio_sf_boston_nyc.yaml')
    parser.add_argument('--output',required=True)
    parser.add_argument('--family',required=True)
    parser.add_argument('--live')
    args=parser.parse_args()
    if os.name=='nt':
        import ctypes
        kernel=ctypes.windll.kernel32
        kernel.GetCurrentProcess.restype=ctypes.c_void_p
        kernel.SetProcessAffinityMask.argtypes=[ctypes.c_void_p,ctypes.c_size_t]
        kernel.SetPriorityClass.argtypes=[ctypes.c_void_p,ctypes.c_ulong]
        assert kernel.SetProcessAffinityMask(kernel.GetCurrentProcess(),3)
        assert kernel.SetPriorityClass(kernel.GetCurrentProcess(),0x4000)
    raise SystemExit(0 if audit(args.studio,args.output,args.family,args.live) else 1)
