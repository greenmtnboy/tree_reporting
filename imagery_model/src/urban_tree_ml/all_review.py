"""Combined train/validation review, never test; scores retain their source run."""
import json
import polars as pl
from urban_tree_ml.mosaic_coverage import missing_chips
from urban_tree_ml.review_assignments import assignment_choices, geographic_assignments, unpredicted_assignment_rows
from urban_tree_ml.queue_navigation import review_split_ids
from urban_tree_ml.catalog_imagery import matching_catalog_summary


def combined_summary(catalog, run_id, context, contexts=None, *, records=None, validation_only=False,
                     all_imagery=False, scope_city=None):
    # Discover once per request, without computing every historical run's goal
    # metrics just to find the four cohorts belonging to the selected model.
    if records is None:
        records = (catalog.records() if hasattr(catalog, 'records')
                   else catalog.summary()['runs'])
    from urban_tree_ml.temporal_studio import record_acquisition, acquisition_records, tile_label
    all_records = records
    if all_imagery:
        selected = catalog.bundle(run_id)
        parts=[]; groups=set()
        for record in records:
            city=record['city'].lower(); acquisition=record_acquisition(record)
            group=(city,acquisition)
            if (record['training_run_id'] != selected.training_run_id or group in groups
                    or (scope_city and scope_city != 'all' and city != scope_city)
                    or record['metrics'].get('split') not in ('train','validation')
                    or (validation_only and record['metrics'].get('split') != 'validation')):
                continue
            owner=(contexts or {}).get(acquisition or city)
            if owner is None and acquisition:
                # A city's primary dated acquisition is registered under its city code.
                primary = (contexts or {}).get(city)
                if getattr(primary, 'acquisition_id', None) == acquisition:
                    owner = primary
            if owner is None:
                continue  # Unregistered imagery cannot be opened for editing.
            groups.add(group)
            part=combined_summary(catalog,record['run_id'],owner,records=records,validation_only=validation_only)
            prefix=acquisition or city
            remap=lambda key:prefix+':'+key.split(':',1)[1]
            part['chips']=[{**row,'key':prefix+':'+row['chip_id'],'acquisition_filter':acquisition or ''}
                           for row in part['chips']]
            part['assignments']['chips']={remap(k):v for k,v in part['assignments']['chips'].items()}
            part['no_imagery']=[remap(k) for k in part['no_imagery']]
            part['review_scope']=dict(city=city,acquisition=acquisition or '',key_prefix=prefix)
            parts.append(part)
        # A geographic review assignment can include a prepared year which has
        # no prediction pass yet. Open its existing scenes, never another year's
        # inference or invented zero-miss scores.
        for key, owner in (contexts or {}).items():
            acquisition = getattr(owner, 'acquisition_id', None)
            city = getattr(owner, 'city', key).lower()
            if (key != acquisition or (city, acquisition) in groups
                    or (scope_city and scope_city != 'all' and city != scope_city)):
                continue
            rows, assigned = unpredicted_assignment_rows(owner)
            if validation_only:
                rows = [row for row in rows if row['split'] == 'validation']
            if not rows:
                continue
            parts.append(dict(chips=[{**row, 'city':city, 'key':acquisition+':'+row['chip_id'],
                'acquisition_filter':acquisition, 'acquisition_id':acquisition,
                'display_label':tile_label(city,row['chip_id'],acquisition)} for row in rows],
                assignments={'chips':{acquisition+':'+row['chip_id']:assigned[row['chip_id']] for row in rows}},
                no_imagery=[],review_scope=dict(city=city,acquisition=acquisition,key_prefix=acquisition)))
        return {**selected.summary(),'city':scope_city or 'all','all_imagery':True,
                'cities':sorted({p['review_scope']['city'] for p in parts}),
                'review_scopes':[p['review_scope'] for p in parts],
                'chips':[c for p in parts for c in p['chips']],
                'assignments':{'chips':{k:v for p in parts for k,v in p['assignments']['chips'].items()}},
                'assignment_options':assignment_choices(context.config.paths.root, records,
                    {p['review_scope']['city'] for p in parts}),
                'no_imagery':[k for p in parts for k in p['no_imagery']],
                'curation_available':True,'cohort':'train + validation'}
    selected_record = next((r for r in records if r['run_id'] == run_id), None)
    acquisition = record_acquisition(selected_record) if selected_record else None
    records = acquisition_records(records, acquisition)
    if acquisition:
        contexts = None
    if contexts is not None:
        selected = catalog.bundle(run_id)
        parts = []
        for city, owner in contexts.items():
            record = next((r for r in records
                           if r['training_run_id'] == selected.training_run_id
                           and r['city'].lower() == city.lower()
                           and (not validation_only or r['metrics'].get('split') == 'validation')
                           and r['metrics'].get('split') in ('train', 'validation')), None)
            if record:
                parts.append(combined_summary(catalog, record['run_id'], owner, records=records,
                                              validation_only=validation_only))
        return {**selected.summary(), 'city': 'all', 'cities': [p['city'].lower() for p in parts],
                'assignment_options': assignment_choices(context.config.paths.root, records, contexts),
                'chips': [c for p in parts for c in p['chips']],
                'assignments': {
                    'snapshots': {p['city'].lower(): p['assignments'].get('snapshot') for p in parts},
                    'chips': {key: item for p in parts for key, item in p['assignments']['chips'].items()},
                },
                'no_imagery': [key for p in parts for key in p['no_imagery']],
                'curation_available': True, 'cohort': 'train + validation'}
    selected = catalog.bundle(run_id)
    owner_city = getattr(context, 'city', None)
    if owner_city and selected.config.inventory.city.lower() != owner_city.lower():
        raise ValueError('Selected review model belongs to another city')
    result = selected.summary()
    rows, seen = [], set()
    for record in records:
        split = record['metrics'].get('split')
        if split not in ('train', 'validation'):
            continue
        if validation_only and split != 'validation':
            continue
        if (record['training_run_id'] != selected.training_run_id
                or record['city'] != selected.config.inventory.city):
            continue
        bundle = catalog.bundle(record['run_id'])
        display = (dict(chip_pixels=bundle.config.imagery.chip_pixels,
                        output_stride=bundle.config.targets.output_stride,
                        resolution_m=bundle.config.imagery.resolution_m)
                   if hasattr(bundle.config, 'imagery') else bundle.summary()['display'])
        if display != result['display']:
            continue
        for chip in bundle.chips:
            if chip['chip_id'] in seen:
                raise ValueError('Chip appears in multiple splits; cannot merge safely')
            seen.add(chip['chip_id'])
            rows.append({**chip, 'split': split, 'inference_run': record['run_id']})
        if split == 'validation' and record.get('dataset'):
            try:
                grid = review_split_ids(context, record, split)
            except FileNotFoundError:
                grid = set()  # Historical downloads may only contain inference.
            extra = catalog.review_backfill(record) if record.get('evaluation_dir') is not None else None
            if extra is not None:
                for chip in extra.chips:
                    chip_id = chip['chip_id']
                    if chip_id not in grid or chip_id in seen:
                        raise ValueError('Backfill chip is outside the unevaluated validation grid')
                    rows.append({**chip, 'split': split, 'inference_run': record['run_id'], 'review_only': True})
                    seen.add(chip_id)
            for chip_id in sorted(grid - seen):
                seen.add(chip_id)
                rows.append(dict(chip_id=chip_id, split=split, review_run=record['run_id'],
                                 inference_run=None, missed=None, detection_f1=None,
                                 error_score=None, false_positive=None, ground_truth=None))
    # Older runs may have no training inference. Include chip metadata without
    # fabricating misses or silently substituting a different model's predictions.
    candidates = []
    for path in (() if validation_only else (context.config.paths.root / 'chips').glob('*/summary.json')):
        try:
            summary = json.loads(path.read_text())
            if matching_catalog_summary(context.config.paths.root, summary, context.raster):
                if (path.parent / 'chips.parquet').exists():
                    candidates.append(path)
        except (OSError, ValueError):
            continue
    if candidates:
        directory = max(candidates, key=lambda p: p.stat().st_mtime).parent
        chips = (pl.scan_parquet(directory / 'chips.parquet').filter(pl.col('split') == 'train')
                 .select('chip_id').collect())
        for chip_id in chips['chip_id']:
            if str(chip_id) not in seen:
                rows.append(dict(chip_id=str(chip_id), split='train', inference_run=None,
                                 missed=None, detection_f1=None, error_score=None,
                                 false_positive=None, ground_truth=None))
    city = selected.config.inventory.city.lower()
    # Assignments share the same city-qualified identity as tiles and navigation.
    # A chip ID alone can refer to different physical tiles in different cities.
    assignments = result.get('assignments') or {}
    portable = geographic_assignments(context)
    portable = {row['chip_id']:portable[row['chip_id']] for row in rows
                if row['chip_id'] in portable and row['split'] == portable[row['chip_id']]['split']}
    assignments = {**assignments, 'chips':{**assignments.get('chips',{}), **portable}}
    missing = missing_chips(selected.raster_path, selected.config.imagery.chip_pixels,
                            (row['chip_id'] for row in rows)) if hasattr(selected, 'raster_path') else set()
    rows = [row for row in rows if row['chip_id'] not in missing]
    visible_ids = {c['chip_id'] for c in rows}
    assignments = {**assignments, 'chips': {
        city + ':' + chip: item for chip, item in assignments.get('chips', {}).items()
        if chip in visible_ids
    }}
    from urban_tree_ml.temporal_studio import tile_label
    display_acquisition = acquisition or getattr(context, 'acquisition_id', None)
    return {**result, 'assignments': assignments,
            'no_imagery': [city + ':' + chip for chip in sorted(missing)],
            'assignment_options': assignment_choices(context.config.paths.root, all_records, [city]),
            'chips': [{**c, 'city': city, 'key': city + ':' + c['chip_id'],
                       'acquisition_filter': acquisition or '',
                       'acquisition_id': display_acquisition,
                       'display_label': tile_label(city, c['chip_id'], display_acquisition)} for c in rows],
            'curation_available': True, 'cohort': 'train + validation'}


ALL_REVIEW_JS = r'''
function allChipRoute(chip,queueId){
 const row=state.chips.find(c=>(c.key||c.chip_id)===chip),u=new URL(row.scene_id?'/registration':row.split==='train'?'/curate-training':'/curate',location.origin);
 u.search=new URLSearchParams(location.search);
 u.searchParams.set('city',row.city||state.city.toLowerCase());u.searchParams.set('chip',row.chip_id);
 u.searchParams.set('run',row.inference_run||row.review_run||'');u.searchParams.set('return',location.pathname+location.search);u.searchParams.set('queue',queueId);
 if(row.acquisition_filter)u.searchParams.set('acquisition',row.acquisition_filter);else u.searchParams.delete('acquisition');
 if(row.scene_id){u.searchParams.set('scene',row.scene_id);u.searchParams.set('queue_chip',row.chip_id);u.searchParams.set('fullscreen','1');u.searchParams.delete('compare_run');u.searchParams.set('source_predictions','unavailable')}
 if(!queueId)u.searchParams.delete('queue');
 return u.pathname+u.search;
}
function saveAllReviewQueue(storage,id,rows,city,returnTo){
 const prefix='validation-curation:',key=prefix+id;
 const temporal=rows.some(c=>c.acquisition_filter);
 const payload=JSON.stringify({split:'all',version:temporal?4:3,city,returnTo,
  chips:rows.map(c=>c.chip_id),routes:rows.map(c=>temporal?
    [c.split==='train'?0:1,c.city||city,c.acquisition_filter||'',c.inference_run||c.review_run||'',c.scene_id||'']:
    [c.split==='train'?0:1,c.city||city])});
 try{
  // Only disposable All-view navigation queues. Never touch annotation drafts,
  // other application storage, or the original train/validation queues.
  const previous=[];
  for(let i=0;i<storage.length;i++){
   const k=storage.key(i);if(!k?.startsWith(prefix)||k===key)continue;
   try{if(JSON.parse(storage.getItem(k))?.split==='all')previous.push(k)}catch{}
  }
  while(previous.length>2)storage.removeItem(previous.shift());
  try{storage.setItem(key,payload)}catch(error){
   // Recover old, oversized queues left by the previous implementation.
   for(const k of previous)storage.removeItem(k);
   storage.setItem(key,payload);
  }
  return true;
 }catch{return false}
}
function allReviewSort(a,b,key){
 const av=a[key==='worst'?'detection_f1':key],bv=b[key==='worst'?'detection_f1':key];
 if(av==null||bv==null)return av==null?(bv==null?a.chip_id.localeCompare(b.chip_id):1):-1;
 return (key==='worst'?av-bv:bv-av)||a.chip_id.localeCompare(b.chip_id);
}
function matchesAllReviewAssignment(chip,assignments,tag){
 const assignment=assignments[chip.key||((chip.city||state.city).toLowerCase()+':'+chip.chip_id)];
 return tag==='all'||(tag==='assigned'?!!assignment:assignment?.queue===tag);
}
function assignmentReviewUrl(option){
 const url=new URL(location.href);
 url.searchParams.set('run',option.run_id);
 url.searchParams.set('assignment',option.queue);
 url.searchParams.set('split','all');
 for(const key of ['chip','queue','queue_chip'])url.searchParams.delete(key);
 // A historical queue's owning run uses original imagery.
 url.searchParams.delete('acquisition');url.searchParams.delete('compare_run');
 return url.pathname+url.search;
}
async function initAllReview(){
 const validationOnly=(pageParams.get('split')||'validation')==='validation';
 const response=await fetch('/api/model/all-summary?'+new URLSearchParams({run:requestedRun||'',city:pageParams.get('city')||'',split:validationOnly?'validation':'all',...(pageParams.get('acquisition')?{acquisition:pageParams.get('acquisition')}:{})}));
 if(!response.ok)throw Error('Could not load combined chip review. Retry to reload.');
 state=await response.json();
 chart();
 reviewLoading('Loading review status for the selected cities…');
 curationStatus={};
 const scopes=state.review_scopes||(state.cities||[state.city.toLowerCase()]).map(city=>({city,key_prefix:city,acquisition:pageParams.get('acquisition')||''}));
 await Promise.all(scopes.map(async scope=>{
  const status=await fetch('/api/curation-status?'+new URLSearchParams({city:scope.city,...(scope.acquisition?{acquisition:scope.acquisition}:{})}));
  if(status.ok)for(const [id,value] of Object.entries((await status.json()).chips||{}))curationStatus[scope.key_prefix+':'+id]=value;
 }));
 const trainingOnly=['train','training-all'].includes(pageParams.get('split'));
 document.querySelector('h1').textContent=validationOnly?'Validation chip review':trainingOnly?'Training chip review':'Chip review';
 document.querySelector('header .lede').textContent=(trainingOnly?'Training chips':'Training / validation')+' · globally ranked across selected cities · test excluded · missing inference sorts last';
 document.querySelectorAll('.metrics').forEach(e=>e.hidden=true);
 $('run-detail').textContent=`${state.city} · ${state.training_run_id} · ${state.chips.filter(c=>c.split==='train').length} train / ${state.chips.filter(c=>c.split==='validation').length} validation · ${state.chips.filter(c=>c.provisional_reference).length} with city inventory references · ${state.chips.filter(c=>c.prediction_only&&!c.provisional_reference).length} without reference labels`;
 if(validationOnly){
  document.querySelector('header .lede').textContent='Validation split · chips with saved predictions can be ranked by model errors';
  $('run-detail').textContent=`${state.city} · ${state.training_run_id} · ${state.chips.length} validation chips · ${state.chips.filter(c=>c.inference_run).length} with saved inference`;
 }
 applyReviewFilterDefaults(state);
 // Ranking and overlays share the selected working confidence and fixed radius.
 const assignments=state.assignments?.chips||{},tags=[...new Set(Object.values(assignments).map(a=>a.queue))];
 const otherAssignments=new Map(),labels={all:'All assignments',assigned:'All assigned'};
 const portableTags=new Set(Object.values(assignments).filter(a=>a.geographic).map(a=>a.queue));
 const otherOptions=(state.assignment_options||[]).filter(option=>option.training_run_id!==state.training_run_id&&!portableTags.has(option.queue));
 for(const option of otherOptions){
  const value=JSON.stringify([option.run_id,option.queue]);otherAssignments.set(value,option);
  const duplicate=tags.includes(option.queue)||otherOptions.filter(other=>other.queue===option.queue).length>1;
  labels[value]=option.queue+(duplicate?` · ${option.training_run_id}`:'');
 }
 setReviewOptions('assignment',['all','assigned',...tags,...otherAssignments.keys()],v=>labels[v]||v);
 let generation=0;
 const queueId=crypto.randomUUID();
 async function renderAll(){
  const signal=startOverlayRender();
  const gen=++generation,key=$('sort').value,tag=$('assignment').value;
  if(otherAssignments.has(tag)){location.href=assignmentReviewUrl(otherAssignments.get(tag));return;}
  $('assignment-progress').textContent='Calculating ranking at selected confidence and radius…';
  reviewLoading('Ranking chips at the selected confidence and radius…');
  let ranked;
  try{ranked=await rankedReviewChips(true,signal)}catch(error){if(gen===generation){$('assignment-progress').textContent=error.message;reviewLoadError(error)}return}
  if(gen!==generation)return;
  state.chips=ranked;
const rows=state.chips.filter(c=>(!pageParams.get('chip')||c.chip_id===pageParams.get('chip'))&&(pageParams.get('split')!=='prediction-only'||c.prediction_only)&&(!['training-all','train'].includes(pageParams.get('split'))||c.split==='train')&&(pageParams.get('split')!=='validation'||c.split==='validation')&&(matchesReviewStatus(curationStatus[c.key]))&&matchesAllReviewAssignment(c,assignments,tag)).sort((a,b)=>allReviewSort(a,b,key));
  let queueSaved=false;
  try{queueSaved=saveAllReviewQueue(sessionStorage,queueId,rows,state.city.toLowerCase(),location.pathname+location.search)}catch{}
  const id=queueSaved?queueId:'';
  $('assignment-progress').textContent=`${rows.length} match filters · ${rows.filter(c=>!c.inference_run).length} without inference · dynamic counts at ${$('radius').value} m fixed radius / ${$('threshold').value} confidence`;
  if(state.no_imagery?.length)$('assignment-progress').textContent+=' · '+state.no_imagery.length+' tiles with insufficient imagery coverage excluded';
  if(!queueSaved)$('assignment-progress').textContent+=' · Browser queue storage unavailable: chips can still open, but Next is unavailable';
  const host=$('gallery');host.innerHTML='';
  const requested=pageParams.get('chip');
  if(!rows.length&&requested&&state.no_imagery?.some(key=>key.endsWith(':'+requested))){
    host.textContent='This tile has insufficient source imagery coverage.';
  }
  const shown=rows.slice(0,+$('limit').value),slots=shown.map(c=>{const slot=document.createElement('div');slot.textContent=(c.display_label||c.chip_id)+' · Loading…';host.append(slot);return slot});
  let cursor=0;
  let loadedCards=0;
  reviewLoading(`Loading chip overlays · 0 / ${shown.length}`);
  async function worker(){while(cursor<shown.length&&gen===generation){const index=cursor++,c=shown[index],slot=slots[index],url=allChipRoute(c.key,id);
   try{
    let data=null;if(c.inference_run){data=chipCache.get(c.key);if(!data){const r=await fetch('/api/model/chip/'+c.chip_id+'?'+new URLSearchParams({run:c.inference_run}),{signal});if(!r.ok)throw Error('prediction load');data=await r.json();chipCache.set(c.key,data)}}
    if(gen!==generation)return;
    const image=c.scene_image?'/city-assets/'+encodeURIComponent(c.acquisition_filter)+'/'+c.scene_image
      :c.inference_run?'/api/model/image/'+c.chip_id+'.png?'+new URLSearchParams({run:c.inference_run})
      :c.review_run?'/api/review-next-image?'+new URLSearchParams({run:c.review_run,city:c.city,chip:c.chip_id,split:c.split})
      :'/api/training-image?'+new URLSearchParams({city:c.city,chip:c.chip_id});
    slot.innerHTML=`<article class="card"><div class="card-head"><strong>${escapeHtml(c.display_label||c.city+' · '+c.chip_id)}</strong><span>${c.split}</span><a class="action-link" href="${escapeHtml(url)}">Curate</a></div><a href="${escapeHtml(url)}"><div class="image"><img loading="lazy" src="${escapeHtml(image)}">${data?markerLayers(data,matchChip(data)):''}</div></a><div class="facts">${c.provisional_reference?`${c.missed} missed reference trees · ${c.ground_truth} inventory trees · review for this year`:c.prediction_only?'Prediction snapshot has no reference labels':c.missed==null?'Inference unavailable':`${c.missed} missed targets · ${c.ground_truth} targets`} · ${curationStatus[c.key]?.reviewed?'Reviewed':'Not done'}</div></article>`;
    bindTooltips(slot);
    window.attachChipTags?.(slot,c.inference_run,c.chip_id,c.city);
   }catch(error){if(gen===generation)slot.textContent=c.chip_id+' · Could not load; change a filter to retry'}
   if(gen===generation)$('review-load-text').textContent=`Loading chip overlays · ${++loadedCards} / ${shown.length}`;
  }}
  await Promise.all(Array.from({length:Math.min(4,shown.length)},worker));
  if(gen===generation)reviewLoaded();
 }
 reviewFiltersChanged=renderAll;
 await renderAll();
}
'''
