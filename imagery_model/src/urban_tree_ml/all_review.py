"""Combined train/validation review, never test; scores retain their source run."""
import json
import pandas as pd


def combined_summary(catalog, run_id, context, contexts=None):
    if contexts is not None:
        selected = catalog.bundle(run_id)
        parts = []
        for city, owner in contexts.items():
            record = next((r for r in catalog.summary()['runs']
                           if r['training_run_id'] == selected.training_run_id
                           and r['city'].lower() == city.lower()
                           and r['metrics'].get('split') in ('train', 'validation')), None)
            if record:
                parts.append(combined_summary(catalog, record['run_id'], owner))
        return {**selected.summary(), 'city': 'all', 'cities': [p['city'].lower() for p in parts],
                'chips': [c for p in parts for c in p['chips']], 'assignments': {},
                'curation_available': True, 'cohort': 'train + validation'}
    selected = catalog.bundle(run_id)
    result = selected.summary()
    rows, seen = [], set()
    for record in catalog.summary()['runs']:
        split = record['metrics'].get('split')
        if split not in ('train', 'validation'):
            continue
        if (record['training_run_id'] != selected.training_run_id
                or record['city'] != selected.config.inventory.city):
            continue
        bundle = catalog.bundle(record['run_id'])
        if bundle.summary()['display'] != result['display']:
            continue
        for chip in bundle.chips:
            if chip['chip_id'] in seen:
                raise ValueError('Chip appears in multiple splits; cannot merge safely')
            seen.add(chip['chip_id'])
            rows.append({**chip, 'split': split, 'inference_run': record['run_id']})
    # Older runs may have no training inference. Include chip metadata without
    # fabricating misses or silently substituting a different model's predictions.
    candidates = []
    for path in (context.config.paths.root / 'chips').glob('*/summary.json'):
        try:
            summary = json.loads(path.read_text())
            if str(summary.get('source_raster', '')).replace('\\', '/').split('/')[-1] == context.raster.name:
                if (path.parent / 'chips.parquet').exists():
                    candidates.append(path)
        except (OSError, ValueError):
            continue
    if candidates:
        directory = max(candidates, key=lambda p: p.stat().st_mtime).parent
        chips = pd.read_parquet(directory / 'chips.parquet')
        for chip in chips[chips.split == 'train'].itertuples():
            if str(chip.chip_id) not in seen:
                rows.append(dict(chip_id=str(chip.chip_id), split='train', inference_run=None,
                                 missed=None, detection_f1=None, error_score=None,
                                 false_positive=None, ground_truth=None))
    city = selected.config.inventory.city.lower()
    return {**result, 'chips': [{**c, 'city': city, 'key': city + ':' + c['chip_id']} for c in rows], 'curation_available': True, 'cohort': 'train + validation'}


ALL_REVIEW_JS = r'''
function allChipRoute(chip,queueId){
 const row=state.chips.find(c=>(c.key||c.chip_id)===chip),u=new URL(row.split==='train'?'/curate-training':'/curate',location.origin);
 u.search=new URLSearchParams(location.search);
 u.searchParams.set('city',row.city||state.city.toLowerCase());u.searchParams.set('chip',row.chip_id);
 u.searchParams.set('run',row.inference_run||'');u.searchParams.set('return',location.pathname+location.search);u.searchParams.set('queue',queueId);
 if(!queueId)u.searchParams.delete('queue');
 return u.pathname+u.search;
}
function saveAllReviewQueue(storage,id,rows,city,returnTo){
 const prefix='validation-curation:',key=prefix+id;
 const payload=JSON.stringify({split:'all',version:3,city,returnTo,
  chips:rows.map(c=>c.chip_id),routes:rows.map(c=>[c.split==='train'?0:1,c.city||city])});
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
async function initAllReview(){
 const response=await fetch('/api/model/all-summary?'+new URLSearchParams({run:requestedRun||'',city:pageParams.get('city')||''}));
 if(!response.ok){$('gallery').textContent='Could not load combined chip review';return}
 state=await response.json();
 curationStatus={};
 await Promise.all((state.cities||[state.city.toLowerCase()]).map(async city=>{
  const status=await fetch('/api/curation-status?'+new URLSearchParams({city}));
  if(status.ok)for(const [id,value] of Object.entries((await status.json()).chips||{}))curationStatus[city+':'+id]=value;
 }));
 const trainingOnly=['train','training-all'].includes(pageParams.get('split'));
 document.querySelector('h1').textContent=trainingOnly?'Training chip review':'Chip review';
 document.querySelector('header .lede').textContent=(trainingOnly?'Training chips':'Training / validation')+' · globally ranked across selected cities · test excluded · missing inference sorts last';
 document.querySelectorAll('.metrics,.workspace').forEach(e=>e.hidden=true);
 $('run-detail').textContent=`${state.city} · ${state.training_run_id} · ${state.chips.filter(c=>c.split==='train').length} training / ${state.chips.filter(c=>c.split==='validation').length} validation chips`;
 $('radius').innerHTML=Object.keys(state.metrics.metrics_by_match_radius_m).map(r=>`<option>${r}</option>`).join('');
 $('threshold').value=state.metrics.confidence_threshold;
 $('threshold-value').textContent=state.metrics.confidence_threshold;
 // Ranking uses the saved evaluation counts, consistent across both splits.
 $('radius').closest('label').hidden=true;$('threshold').closest('label').hidden=true;
 const old=$('assignment'),replacement=old.cloneNode(false);old.replaceWith(replacement);
 replacement.innerHTML='<option value="all">All assignments</option>';
 const assignments=state.assignments?.chips||{},tags=[...new Set(Object.values(assignments).map(a=>a.queue))];
 for(const tag of tags)replacement.add(new Option(tag,tag));
 $('review-status').value=pageParams.get('review-status')||(pageParams.get('unreviewed')==='1'?'pending':'all');
 window.studioViewState?.restore();
 $('radius').value=Object.keys(state.metrics.metrics_by_match_radius_m).sort((a,b)=>+a-+b)[0];
 $('threshold').value=state.metrics.confidence_threshold;
 let generation=0;
 const queueId=crypto.randomUUID();
 async function renderAll(){
  const gen=++generation,key=$('sort').value,tag=$('assignment').value;
  const rows=state.chips.filter(c=>(!['training-all','train'].includes(pageParams.get('split'))||c.split==='train')&&(pageParams.get('split')!=='validation'||c.split==='validation')&&(matchesReviewStatus(curationStatus[c.key]))&&(tag==='all'||assignments[c.chip_id]?.queue===tag)).sort((a,b)=>allReviewSort(a,b,key));
  let queueSaved=false;
  try{queueSaved=saveAllReviewQueue(sessionStorage,queueId,rows,state.city.toLowerCase(),location.pathname+location.search)}catch{}
  const id=queueSaved?queueId:'';
  $('assignment-progress').textContent=`${rows.length} match filters · ${rows.filter(c=>!c.inference_run).length} without inference · saved counts at ${$('radius').value} m / ${$('threshold').value} confidence`;
  if(!queueSaved)$('assignment-progress').textContent+=' · Browser queue storage unavailable: chips can still open, but Next is unavailable';
  const host=$('gallery');host.innerHTML='';
  const shown=rows.slice(0,+$('limit').value),slots=shown.map(c=>{const slot=document.createElement('div');slot.textContent=c.chip_id+' · Loading…';host.append(slot);return slot});
  let cursor=0;
  async function worker(){while(cursor<shown.length&&gen===generation){const index=cursor++,c=shown[index],slot=slots[index],url=allChipRoute(c.key,id);
   try{
    let data=null;if(c.inference_run){data=chipCache.get(c.key);if(!data){const r=await fetch('/api/model/chip/'+c.chip_id+'?'+new URLSearchParams({run:c.inference_run}));if(!r.ok)throw Error('prediction load');data=await r.json();chipCache.set(c.key,data)}}
    if(gen!==generation)return;
    const image=c.inference_run?'/api/model/image/'+c.chip_id+'.png?'+new URLSearchParams({run:c.inference_run}):'/api/training-image?'+new URLSearchParams({city:c.city,chip:c.chip_id});
    slot.innerHTML=`<article class="card"><div class="card-head"><strong>${c.city} · ${c.chip_id}</strong><span>${c.split}</span><a class="action-link" href="${escapeHtml(url)}">Curate</a></div><a href="${escapeHtml(url)}"><div class="image"><img loading="lazy" src="${escapeHtml(image)}">${data?markerLayers(data,matchChip(data)):''}</div></a><div class="facts">${c.missed==null?'Inference unavailable':`${c.missed} missed targets · ${c.ground_truth} targets`} · ${curationStatus[c.key]?.reviewed?'Reviewed':'Not done'}</div></article>`;
    bindTooltips(slot);
   }catch(error){slot.textContent=c.chip_id+' · Could not load; change a filter to retry'}
  }}
  await Promise.all(Array.from({length:Math.min(4,shown.length)},worker));
 }
 for(const id of ['sort','limit','assignment','review-status'])$(id).addEventListener('change',()=>{const u=new URL(location.href);for(const k of ['sort','limit','assignment'])u.searchParams.set(k,$(k).value);u.searchParams.set('review-status',$('review-status').value);u.searchParams.delete('unreviewed');history.replaceState(null,'',u);renderAll()});
 await renderAll();
}
'''
