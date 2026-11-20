"""Small, dependency-free curation targeting view and reusable tag editor."""
import json


TAG_EDITOR_JS = r'''<style>.chip-tag-editor{position:relative;font-size:13px}.chip-tag-editor>div{padding:10px;background:#20362b;color:#eef5ee}.card-head .chip-tag-editor>div{position:absolute;right:0;top:25px;z-index:120;width:340px;box-shadow:0 4px 15px #000}.chip-tag-editor select{max-width:100%;min-width:240px}.chip-tag-editor input{max-width:95%}.chip-tag-editor button{margin:5px}</style><script>
window.attachChipTags=function(host,run,chip,city){
 if(!run||!chip||host.querySelector('.chip-tag-editor'))return;
 const panel=document.createElement('details');panel.className='chip-tag-editor';
 const title=document.createElement('summary');title.textContent='Chip tags';panel.append(title);
 const content=document.createElement('div');panel.append(content);host.append(panel);
 let loaded=false,current=null;
 const query=new URLSearchParams({run,chip,city});
 async function load(){
  content.textContent='Loading tags…';
  try{const response=await fetch('/api/chip-tags?'+query);const data=await response.json();
   if(!response.ok)throw Error(data.error||'Could not load tags');current=data;loaded=true;render();
  }catch(e){loaded=false;content.textContent=e.message;const retry=document.createElement('button');retry.textContent='Retry';retry.onclick=load;content.append(retry)}
 }
 function render(){
  content.replaceChildren();
  const select=document.createElement('select');select.multiple=true;select.size=7;select.setAttribute('aria-label','Chip tagging categories');
  for(const tag of current.categories){const option=new Option(tag,tag,false,current.tags.includes(tag));select.add(option)}
  const custom=document.createElement('input');custom.placeholder='Additional tags, comma separated';custom.setAttribute('aria-label','Additional chip categories');
  const help=document.createElement('p');help.textContent='Multi-select with Ctrl/Cmd. Tags follow this imagery footprint across models, not training labels.';
  const save=document.createElement('button');save.type='button';save.textContent='Save tags';
  const message=document.createElement('span');message.setAttribute('role','status');
  const reload=document.createElement('button');reload.type='button';reload.textContent='Reload saved tags';reload.onclick=load;
  save.onclick=async()=>{save.disabled=true;reload.disabled=true;message.textContent='Saving…';
   const tags=[...select.selectedOptions].map(o=>o.value).concat(custom.value.split(',').map(s=>s.trim()).filter(Boolean));
   try{const r=await fetch('/api/chip-tags?'+query,{method:'PUT',headers:{'Content-Type':'application/json','X-Studio-Tags':'1'},body:JSON.stringify({tags,revision:current.revision})});
    const data=await r.json();if(!r.ok)throw Error(data.error||'Save failed');current=data;render();
    title.textContent='Chip tags'+(data.tags.length?' · '+data.tags.join(', '):' · none');
    window.dispatchEvent(new CustomEvent('chip-tags-saved'));
   }catch(e){message.textContent=e.message}finally{save.disabled=false;reload.disabled=false}
  };
  content.append(help,select,document.createElement('br'),custom,save,reload,message);
 }
 panel.addEventListener('toggle',()=>{if(panel.open&&!loaded)load()});
};
</script>'''


def inject_editor(html, target=None):
    script = TAG_EDITOR_JS
    if target:
        args = ','.join(json.dumps(value).replace('<', '\\u003c') for value in target)
        script += '<script>(()=>{const mount=()=>{const host=document.querySelector(".card .card-head");if(host)window.attachChipTags(host,' + args + ');};mount();const cards=document.getElementById("cards");if(cards)new MutationObserver(mount).observe(cards,{childList:true});})();</script>'
    return html.replace('</body>', script + '</body>')


SPECIES_TABLE_JS = r'''
function speciesTableRows(rows, filter) {
 const rate=(a,b)=>b?a/b:null;
 const value=(r,key)=>({accuracy:rate(r.correct,r.matched),top5_accuracy:rate(r.top5,r.top5_samples),recall:rate(r.correct,r.labeled),name:r.city+' / '+r.species}[key]??r[key]??null);
 const search=(filter.search||'').trim().toLowerCase(), descending=filter.direction==='desc';
 const selected=rows.filter(r=>(!search||(r.city+' '+r.species).toLowerCase().includes(search))&&
  r.labeled>=filter.minLabeled&&r.matched>=filter.minMatched&&r.chips>=filter.minChips&&
  (filter.kind==='all'||(filter.kind==='vegetation')===r.category)&&
  (filter.maxAccuracy===null||(r.matched>0&&100*r.correct/r.matched<=filter.maxAccuracy)));
 return selected.sort((a,b)=>{
  const av=value(a,filter.sort),bv=value(b,filter.sort);
  if(av==null&&bv!=null)return 1;if(bv==null&&av!=null)return -1;
  const delta=av==null?0:typeof av==='string'?av.localeCompare(bv):av-bv;
  return (descending?-delta:delta)||(a.city+' '+a.species).localeCompare(b.city+' '+b.species);
 });
}
'''


INSIGHTS_HTML = r'''<!doctype html><html><head><meta charset="utf-8"><title>Curation insights</title>
<style>body{background:#101b17;color:#dfebe2;font:14px system-ui;margin:0}main{padding:20px}a{color:#9dddcb}h2{margin-top:28px}.controls{display:flex;gap:12px;flex-wrap:wrap}label{display:flex;flex-direction:column;gap:4px}select,input,button{background:#21382d;color:#eef6ee;border:1px solid #658373;padding:7px;border-radius:4px}table{border-collapse:collapse;width:100%;font-size:13px}th,td{padding:8px;text-align:left;border-bottom:1px solid #334b3d}th{position:sticky;top:0;background:#203629}.table{overflow:auto;max-height:480px}.muted{color:#a7bbaa}.warning{color:#ffba88}.chip-tag-editor{padding:10px;background:#20362b}.chip-tag-editor select{min-width:250px}button{cursor:pointer}#progress{padding:12px 0}</style>
<style>
.loading-panel{display:flex;align-items:center;gap:20px;margin:22px 0;padding:28px;border:2px solid #7dd4ac;border-radius:10px;background:#1c382b;font-size:17px;min-height:110px}.loading-panel[hidden]{display:none}.loading-panel h2{margin:0 0 8px}.loading-panel p{margin:8px 0}.loading-spinner{width:38px;height:38px;flex-shrink:0;border:5px solid #3c6350;border-top-color:#a0f0c5;border-radius:50%;animation:insight-spin 1s linear infinite}@keyframes insight-spin{to{transform:rotate(360deg)}}@media(prefers-reduced-motion:reduce){.loading-spinner{animation:none}}.loading-panel.failed{border-color:#f7aa85}.loading-panel.failed .loading-spinner{display:none}.species-controls{margin:12px 0;align-items:end}.species-controls input[type=number]{width:105px}.species-sort{border:0;padding:0;background:transparent;font-weight:700;text-align:left}.species-sort:focus-visible{outline:2px solid #b4f0ca;outline-offset:4px}#species-count{margin:10px 0}button:disabled{opacity:.55;cursor:wait}section[hidden]{display:none}
</style></head><body><main><h1>Curation insights</h1>
<p>Target curation by city, environment and species. Scores use frozen model labels; tags and completion are live. Test excluded.</p>
<div class="controls">
<label>Split<select id="slice-split"><option value="validation">Validation</option><option value="train">Training</option><option value="prediction-only">Prediction only</option></select></label>
<label>Confidence<input id="slice-threshold" type="number" min="0" max="1" step="0.01" value="0.13"></label>
<label>Matching<select id="slice-policy"><option value="crown-half-radius-2-4m">Crown-aware 2–4 m</option><option value="fixed-2m">Fixed 2 m</option><option value="fixed-4m">Fixed 4 m</option></select></label>
<label>Tag<select id="slice-tag"><option value="">All tags</option></select></label>
<label>Species<select id="slice-species"><option value="">All species</option></select></label>
<label>Variants<select id="slice-shifts"><option value="0">Base footprints only</option><option value="1" selected>Include shifted footprints</option></select></label>
<label>Prediction heads<select id="slice-flags"><option value="0">All chips</option><option value="1">Genus/species disagreements</option></select></label>
<button id="slice-load">Update breakdown</button></div>
<div id="progress" role="status">Choose a model to load scores.</div>
<div id="loading-panel" class="loading-panel" role="status" aria-live="polite" hidden><span class="loading-spinner" aria-hidden="true"></span><div><h2 id="loading-title">Calculating curation insights</h2><p id="loading-stage">Reading development predictions…</p><p class="muted"><span id="loading-elapsed" aria-live="off">0 seconds elapsed</span> · First loads can take a while. CPU only; you can keep curating in another tab.</p></div></div>
<p class="muted">One confidence and matching policy applies across slices—no per-tag threshold tuning. Overlapping tags are not additive. Inventory/incomplete labels are not exhaustive ground truth; empty or unsupported denominators show —. Fewer than 20 matched species labels is marked low support. Shifted views do not count as new base coverage.</p>
<section id="insights-results" aria-busy="false" hidden>
<h2>City × tag: coverage and performance</h2><div class="table"><table><thead><tr><th>City / tag</th><th>Chips base / shifted</th><th>Reviewed / complete</th><th>Labeled trees</th><th>Species labels / matched</th><th>Precision / recall</th><th>F1 / F2</th><th>Species top-1 / top-5</th><th>Species end-to-end recall</th><th>Head conflicts</th></tr></thead><tbody id="slice-groups"></tbody></table></div>
<h2>Species breakdown</h2><p class="muted">Rows are grouped by ground-truth species. Top-1 is correct / matched labeled trees; end-to-end recall includes missed trees. Vegetation categories are shown separately from scientific-species totals.</p>
<div class="controls species-controls">
<label>Search species or city<input id="species-search" type="search" placeholder="e.g. Acer or usbos"></label>
<label>Min labeled samples<input id="species-min-labeled" type="number" min="0" step="1" value="0"></label>
<label>Min matched samples<input id="species-min-matched" type="number" min="0" step="1" value="0"></label>
<label>Min chips<input id="species-min-chips" type="number" min="0" step="1" value="0"></label>
<label>Max top-1 accuracy (%)<input id="species-max-accuracy" type="number" min="0" max="100" step="1" placeholder="Any"></label>
<label>Class type<select id="species-kind"><option value="all">All classes</option><option value="scientific">Scientific species</option><option value="vegetation">Vegetation categories</option></select></label>
<button id="species-reset" type="button">Reset species filters</button></div>
<p id="species-count" class="muted" role="status"></p>
<div class="table"><table id="species-table"><thead><tr>
<th aria-sort="none"><button class="species-sort" data-sort="name">City / species</button></th>
<th aria-sort="none"><button class="species-sort" data-sort="chips">Chips</button></th>
<th aria-sort="none"><button class="species-sort" data-sort="labeled">Labeled</button></th>
<th aria-sort="none"><button class="species-sort" data-sort="matched">Matched</button></th>
<th aria-sort="none"><button class="species-sort" data-sort="correct">Correct</button></th>
<th aria-sort="ascending"><button class="species-sort" data-sort="accuracy">Top-1</button></th>
<th aria-sort="none"><button class="species-sort" data-sort="top5_accuracy">Top-5</button></th>
<th aria-sort="none"><button class="species-sort" data-sort="recall">End-to-end recall</button></th>
<th>Common confusions</th></tr></thead><tbody id="slice-species-rows"></tbody></table></div>
<h2 class="warning">⚠ Genus / species disagreement</h2><p>Species implies one genus, but the genus head predicts another. These are internal conflicts, not proof of which output is wrong; they can be flagged without ground truth.</p>
<p class="muted">Conflict rate is flagged / comparable predictions of that species within the selected chips—not an accuracy score. Use the rate alongside raw counts when comparing common and rare species.</p>
<div class="table"><table><thead><tr><th>City / predicted species</th><th>Flagged / comparable predictions</th><th>Conflict rate</th><th>Chips with conflicts</th><th>Conflicting predicted genera</th></tr></thead><tbody id="slice-conflicts"></tbody></table></div>
<h2>Chips to curate</h2><p id="slice-count" class="muted"></p>
<div class="table"><table><thead><tr><th>City / chip</th><th>Tags</th><th>Matched species support</th><th>Species top-1</th><th>Head conflicts</th><th>Review / edit tags</th></tr></thead><tbody id="slice-chips"></tbody></table></div>
</section></main><script>__SPECIES_TABLE_JS__</script><script>
(()=>{
const el=id=>document.getElementById(id),esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const pct=n=>n==null?'—':(100*n).toFixed(1)+'%',ratio=(a,b)=>b?a/b:null;
const params=new URLSearchParams(location.search),controls={split:'slice-split',threshold:'slice-threshold',policy:'slice-policy',tag:'slice-tag',species:'slice-species',shifts:'slice-shifts',flags:'slice-flags'};
for(const [key,id] of Object.entries(controls)){const value=params.get(key);if(value!==null){if(el(id).tagName==='SELECT'&&![...el(id).options].some(o=>o.value===value))el(id).add(new Option(value,value));el(id).value=value}}
const speciesControls={sp_search:'species-search',sp_min_labeled:'species-min-labeled',sp_min_matched:'species-min-matched',sp_min_chips:'species-min-chips',sp_max_accuracy:'species-max-accuracy',sp_kind:'species-kind'};
for(const [key,id] of Object.entries(speciesControls)){if(params.has(key))el(id).value=params.get(key)}
let speciesSort=params.get('sp_sort')||'accuracy',speciesDirection=params.get('sp_dir')==='desc'?'desc':'asc',speciesRows=null;
const sortable=[...document.querySelectorAll('.species-sort')];
if(!sortable.some(b=>b.dataset.sort===speciesSort))speciesSort='accuracy';
for(const button of sortable)button.dataset.label=button.textContent;
let generation=0,timer,elapsedTimer;
function speciesFilters(){return {search:el('species-search').value,minLabeled:Math.max(0,Number(el('species-min-labeled').value)||0),minMatched:Math.max(0,Number(el('species-min-matched').value)||0),minChips:Math.max(0,Number(el('species-min-chips').value)||0),maxAccuracy:el('species-max-accuracy').value===''?null:Math.min(100,Math.max(0,Number(el('species-max-accuracy').value))),kind:el('species-kind').value||'all',sort:speciesSort,direction:speciesDirection}}
function renderSpecies(persist=false){
 if(persist){const q=new URLSearchParams(location.search);for(const [key,id] of Object.entries(speciesControls))q.set(key,el(id).value);q.set('sp_sort',speciesSort);q.set('sp_dir',speciesDirection);history.replaceState(null,'','/insights?'+q)}
 for(const button of sortable){const active=button.dataset.sort===speciesSort;button.closest('th').setAttribute('aria-sort',active?(speciesDirection==='asc'?'ascending':'descending'):'none');button.textContent=button.dataset.label+(active?(speciesDirection==='asc'?' ↑':' ↓'):' ↕')}
 if(speciesRows===null)return;
 const rows=speciesTableRows(speciesRows,speciesFilters());
 el('species-count').textContent=`${rows.length} / ${speciesRows.length} city/species rows · minimums are inclusive · filters apply only to this table · changes are instant, no rescoring`;
 el('slice-species-rows').innerHTML=rows.length?rows.map(r=>`<tr><td><a href="${esc(filterLink(r.city,r.species,el('slice-tag').value))}">${esc(r.city+' / '+r.species)}</a>${r.category?' · vegetation category':''}${r.matched<20?' <span class="warning">low support</span>':''}</td><td>${r.chips}</td><td>${r.labeled}</td><td>${r.matched}</td><td>${r.correct}</td><td>${pct(ratio(r.correct,r.matched))}</td><td>${pct(ratio(r.top5,r.top5_samples))} (n=${r.top5_samples})</td><td>${pct(ratio(r.correct,r.labeled))}</td><td>${esc(r.confusions.map(([s,n])=>s+': '+n).join('; '))}</td></tr>`).join(''):'<tr><td colspan="9">No species meet these filters. Lower the sample minimums or reset the species filters.</td></tr>';
}
for(const id of Object.values(speciesControls))el(id).addEventListener('input',()=>renderSpecies(true));
for(const button of sortable)button.onclick=()=>{if(speciesSort===button.dataset.sort)speciesDirection=speciesDirection==='asc'?'desc':'asc';else{speciesSort=button.dataset.sort;speciesDirection=['name','accuracy','top5_accuracy','recall'].includes(speciesSort)?'asc':'desc'}renderSpecies(true)};
el('species-reset').onclick=()=>{el('species-search').value='';for(const id of ['species-min-labeled','species-min-matched','species-min-chips'])el(id).value='0';el('species-max-accuracy').value='';el('species-kind').value='all';speciesSort='accuracy';speciesDirection='asc';renderSpecies(true)};
renderSpecies();
function loading(active,message='',failed=false){
 const panel=el('loading-panel');panel.hidden=!active;panel.classList.toggle('failed',failed);el('loading-title').textContent=failed?'Could not load insights':'Calculating curation insights';el('loading-stage').textContent=message;
 el('slice-load').disabled=active&&!failed;el('slice-load').textContent=active&&!failed?'Calculating…':failed?'Retry breakdown':'Update breakdown';el('insights-results').setAttribute('aria-busy',String(active&&!failed));
 for(const control of document.querySelectorAll('main > .controls input, main > .controls select'))control.disabled=active&&!failed;
 if(!active||failed)clearInterval(elapsedTimer);
}
function options(id,values,label){const selected=el(id).value;el(id).replaceChildren(new Option(label,''));for(const value of values)el(id).add(new Option(value,value));if(selected&&!values.includes(selected))el(id).add(new Option(selected,selected));el(id).value=selected}
function filterLink(city,species='',tag='',flags='0'){const q=new URLSearchParams(location.search);q.set('city',city);q.set('species',species);q.set('tag',tag==='(all)'?'':tag);q.set('flags',flags);return '/insights?'+q}
function load(){
 clearTimeout(timer);const ticket=++generation,q=new URLSearchParams(location.search);
 const model=el('review-model');if(!model?.value){el('progress').textContent='No completed development model available';return}
 q.set('run',model.value);if(!q.has('city'))q.set('city','all');
 for(const [key,id] of Object.entries(controls))q.set(key,el(id).value);
 history.replaceState(null,'','/insights?'+q);el('progress').textContent='';el('insights-results').hidden=true;loading(true,'Reading frozen development predictions…');
 clearInterval(elapsedTimer);const began=Date.now();const tick=()=>{const seconds=Math.floor((Date.now()-began)/1000);el('loading-elapsed').textContent=`${Math.floor(seconds/60)}m ${seconds%60}s elapsed`};tick();elapsedTimer=setInterval(tick,1000);
 async function poll(){
 try{const response=await fetch('/api/chip-insights?'+q),data=await response.json();if(ticket!==generation)return;
  if(!response.ok||data.state==='error')throw Error(data.error||'Request failed');
  if(data.state==='running'){el('loading-stage').textContent=data.stage;timer=setTimeout(poll,1500);return}
  options('slice-tag',data.tags,'All tags');options('slice-species',data.species_options,'All species');
  el('progress').textContent=`${data.matching_chips} matching chips · ${q.get('split')} · confidence ${q.get('threshold')} · ${q.get('policy')}`;
  el('slice-groups').innerHTML=data.groups.map(r=>`<tr><td><a href="${esc(filterLink(r.city,'',r.tag))}">${esc(r.city+' / '+r.tag)}</a></td><td>${r.base_chips} / ${r.shifted_chips}</td><td>${r.reviewed} / ${r.complete}</td><td>${r.labeled_trees??'—'}</td><td>${r.scientific_labeled} / ${r.species_matched}</td><td>${pct(r.precision)} / ${pct(r.recall)}</td><td>${pct(r.f1)} / ${pct(r.f2)}</td><td>${pct(r.species_accuracy)} / ${pct(r.top5_accuracy)} <small>(top-5 n=${r.top5_samples})</small></td><td>${pct(r.species_recall)}</td><td>${r.genus_disagreements} / ${r.genus_comparable}</td></tr>`).join('');
  speciesRows=data.species;renderSpecies();
  el('slice-conflicts').innerHTML=data.conflicts.map(r=>`<tr><td><a href="${esc(filterLink(r.city,r.species,el('slice-tag').value,'1'))}">${esc(r.city+' / '+r.species)}</a></td><td class="warning">⚠ ${r.count} / ${r.comparable}</td><td>${pct(r.rate)}</td><td>${r.chips}</td><td>${esc(r.predicted_genera.map(([s,n])=>s+': '+n).join('; '))}</td></tr>`).join('');
  el('slice-count').textContent=`Showing up to 200 / ${data.matching_chips}, lowest matched-species accuracy first (missing scores last). Open Chip review to inspect/curate; inspect support as well as error rate.`;
  const host=el('slice-chips');host.replaceChildren();
  for(const r of data.chips){const tr=document.createElement('tr');const link='/model?'+new URLSearchParams({run:r.run,city:r.city,chip:r.chip_id,split:q.get('split'),threshold:q.get('threshold')});
   tr.innerHTML=`<td>${esc(r.city+' / '+r.chip_id)}</td><td>${esc(r.tags.join(', '))||'Untagged'}</td><td>${r.species_matched}${r.species_matched<20?' · low support':''}</td><td>${pct(r.species_accuracy)}</td><td>${r.genus_disagreements?'⚠ '+r.genus_disagreements:0}</td><td><a href="${esc(link)}" target="_blank" rel="noopener">Open chip</a></td>`;host.append(tr);window.attachChipTags(tr.lastElementChild,r.run,r.chip_id,r.city);
  }
  loading(false);el('insights-results').hidden=false;
 }catch(error){if(ticket===generation)loading(true,error.message,true)}
 }
 poll();
}
el('slice-load').onclick=load;
window.addEventListener('chip-tags-saved',()=>{el('progress').textContent='Tags saved. Click Update breakdown to regroup.'});
window.addEventListener('DOMContentLoaded',load);
})();
</script></body></html>'''.replace('__SPECIES_TABLE_JS__', SPECIES_TABLE_JS)
