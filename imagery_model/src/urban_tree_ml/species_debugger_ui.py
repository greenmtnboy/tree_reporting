"""Species-first visual review; lazy crops and explicit conflict-checked saves."""
SPECIES_DEBUGGER_HTML = r'''<!doctype html><html><head><meta charset="utf-8">
<title>Species debugger</title><style>
*{box-sizing:border-box}body{margin:0;background:#101b17;color:#e5eee8;font:14px system-ui}main{max-width:1600px;margin:auto;padding:22px}h1{margin:0 0 8px}a{color:#99dfc7}p{line-height:1.5}.muted{color:#afc1b5}.controls{display:flex;gap:12px;flex-wrap:wrap;padding:16px 0;align-items:end}label{display:flex;flex-direction:column;gap:5px}select,input,button{background:#21382d;color:#eef6ee;border:1px solid #658373;padding:8px;border-radius:5px;font:inherit}button{cursor:pointer}button:disabled{opacity:.5;cursor:wait}.model{max-width:380px}.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:18px}.card{border:1px solid #3e5c4c;background:#192b22;border-radius:10px;overflow:hidden}.card>header,.info,.editor{padding:12px}.card header{display:flex;justify-content:space-between;gap:8px}.badge{font-size:12px;background:#354d40;padding:4px 7px;border-radius:4px}.regressed{background:#723c30}.improved{background:#265e45}.crop{position:relative;aspect-ratio:1;background:repeating-linear-gradient(45deg,#23332b,#23332b 10px,#19251f 10px,#19251f 20px)}.crop img{width:100%;height:100%;object-fit:contain}.crop svg{position:absolute;inset:0;width:100%;height:100%;pointer-events:none}.legend{font-size:12px;color:#baccc0}.info dl{display:grid;grid-template-columns:70px 1fr;gap:6px;margin:12px 0}.info dt{color:#a8c0af}.info dd{margin:0;overflow-wrap:anywhere}.editor{border-top:1px solid #3e5c4c}.editor input{width:100%}.actions{display:flex;flex-wrap:wrap;gap:8px;margin:10px 0}.message{min-height:22px;white-space:pre-wrap}.error{color:#ffc4a8}.pager{display:flex;gap:12px;align-items:center;margin:20px 0}.notice{padding:12px;background:#263d30;border-left:3px solid #b9dab9}.toolbar{position:sticky;top:0;z-index:2;background:#101b17}#status{padding:12px 0}button:focus-visible,a:focus-visible,input:focus-visible,select:focus-visible{outline:2px solid #cef6b1;outline-offset:3px}
.ranking{margin:18px 0;padding:16px;background:#192b22;border:1px solid #3e5c4c;border-radius:8px}.ranking h2{font-size:18px;margin:0 0 8px}.ranking table{border-collapse:collapse;width:100%;text-align:left}.ranking th,.ranking td{padding:8px;border-bottom:1px solid #3e5c4c}.ranking button{padding:5px 8px}.ranking-scroll{overflow-x:auto}.ranking [aria-pressed=true]{outline:2px solid #99dfc7}.ranking details{margin-top:10px}
</style></head><body><main><h1>Species debugger</h1>
<p class="muted">Inspect one species across two models, with nearby imagery. Correct the saved label here; publish it through your usual Publish All flow.</p>
<div class="controls">
<label>Split<select id="split"><option value="validation">Validation</option><option value="train">Training</option></select></label>
<label>Model A<select id="model-a" class="model"></select></label><label>Model B<select id="model-b" class="model"></select></label>
<label>Classification<select id="basis"><option value="matched">At matched detection</option><option value="center">At labeled center · diagnostics only</option></select></label>
<label>Confidence<input id="threshold" type="number" min="0" max="1" step=".01" value=".13"></label>
<label>Matching<select id="policy"><option value="crown-half-radius-2-4m">Crown-aware 2–4 m</option><option value="fixed-2m">Fixed 2 m</option><option value="fixed-4m">Fixed 4 m</option></select></label></div>
<div class="controls">
<label>Species / genus<input id="species" list="species-list" placeholder="Scientific species; blank = all"><datalist id="species-list"></datalist></label>
<label>Include<select id="scope"><option value="any">Labeled or predicted species</option><option value="truth">Labeled species only</option><option value="predicted">Predicted species only</option></select></label>
<label>Outcome<select id="outcome"><option value="all">All outcomes</option><option value="taxonomy-regressed">A correct → B wrong species</option><option value="b-mismatch">B wrong species</option><option value="regressed">A correct → B wrong/missed</option><option value="improved">A wrong/missed → B correct</option><option value="both-correct">Both correct</option><option value="both-wrong">Both wrong/missed</option><option value="unknown-label">Unknown label</option><option value="unmatched">Unmatched predictions</option><option value="unavailable">Classification unavailable</option></select></label>
<label>Surrounding area<select id="span"><option value="24">24 m</option><option value="38.4" selected>38.4 m</option><option value="60">60 m</option></select></label>
<button id="load">Show trees</button></div>
<div class="notice" id="comparison-note">Both models are matched against Model B’s frozen labels on shared tiles. Current edits are separate; old scores are never rewritten. Test is excluded.</div>
<p class="legend">Yellow: labeled center (or unmatched prediction). Green: A’s matched detection. Blue: B’s matched detection. Dashed outline: tile boundary; imagery beyond it belongs to neighboring tiles. Black margins indicate the raster edge or unavailable imagery.</p>
<div id="status" role="status" aria-live="polite"></div>
<section id="ranking" class="ranking" hidden><h2>Top mismatched species</h2>
<p class="muted">Ranked by new classification errors (A correct → B wrong), then B’s total mismatches. Click a species for all B mismatches, or its new-error count for regressions. Counts use frozen labeled species; missing detections, masked samples and unknown labels are excluded from classification errors.</p>
<div id="ranking-table" class="ranking-scroll"></div></section>
<div class="pager"><button id="prev">Previous</button><span id="page-label"></span><button id="next">Next</button></div>
<div id="cards" class="cards"></div></main><script>
(()=>{
const $=id=>document.getElementById(id),q=new URLSearchParams(location.search);
const city=q.get('city')||'__CITY__';let models=[],currentPage=0,result=null,generation=0,timer=null,rankingExpanded=false;
const dirty=new Set();
function lockControls(){for(const el of document.querySelectorAll('.controls input,.controls select,.controls button,.pager button,#ranking button'))el.disabled=dirty.size>0;if(!dirty.size){syncBasis();$('prev').disabled=!result||currentPage<=0;$('next').disabled=!result||currentPage+1>=result.pages;}}window.addEventListener('beforeunload',e=>{if(dirty.size){e.preventDefault();e.returnValue='';}});
function message(text,error=false){$('status').textContent=text;$('status').classList.toggle('error',error)}
async function json(url,options){const r=await fetch(url,options);const d=await r.json();if(!r.ok)throw Error(d.error||`HTTP ${r.status}`);return d}
function query(){return new URLSearchParams({city,split:$('split').value,a:$('model-a').value,b:$('model-b').value,
 threshold:$('threshold').value,policy:$('policy').value,basis:$('basis').value,species:$('species').value.trim(),
 scope:$('scope').value,status:$('outcome').value,page:String(currentPage)})}
function syncBasis(){const allowed=[$('model-a').value,$('model-b').value].every(v=>models.find(m=>m.id===v)?.diagnostic);
 $('basis').options[1].disabled=!allowed;if(!allowed)$('basis').value='matched';}
async function loadOptions(){try{const d=await json('/api/species-debugger/options?'+new URLSearchParams({city,split:$('split').value}));models=d.models;
 for(const id of ['model-a','model-b']){$(id).replaceChildren(...models.map(m=>new Option(m.label,m.id)));}
 const resolve=(value,index)=>models.find(m=>m.id===value)?.id||models.find(m=>value&&m.id.split('::')[0]===value.split('::')[0])?.id||models[index]?.id||models[0]?.id||'';
 $('model-a').value=resolve(q.get('a'),0);$('model-b').value=resolve(q.get('b'),1);
 if(!$('model-a').value)$('model-a').selectedIndex=0;if(!$('model-b').value)$('model-b').selectedIndex=Math.min(1,models.length-1);
 syncBasis();if(!q.has('basis')&&!$('basis').options[1].disabled)$('basis').value='center';
 $('load').disabled=!models.length;message(models.length?'Finding species mismatches…':'No development predictions available.');
 if(models.length)load(true,!q.has('species')&&!q.has('status'));
 }catch(e){message(e.message,true)}}
function text(tag,value,cls){const el=document.createElement(tag);el.textContent=value;if(cls)el.className=cls;return el}
function prediction(p){if(!p)return result.basis==='center'?'Not sampled / masked':'No matched detection';return `${p.species||'Unknown'}${p.confidence==null?'':` · ${(100*p.confidence).toFixed(1)}% class`}${p.score==null?'':` · ${p.score.toFixed(3)} detection`}`}
function marker(svg,x,y,color,r){const c=document.createElementNS('http://www.w3.org/2000/svg','circle');
 for(const[k,v]of Object.entries({cx:x,cy:y,r,fill:'none',stroke:color,'stroke-width':2}))c.setAttribute(k,v);svg.append(c)}
function renderRanking(){
 $('ranking').hidden=false;const host=$('ranking-table');host.replaceChildren();
 if(!result.ranking.length){host.append(text('p','No classified species mismatches in Model B on these shared tiles. Missing detections remain available in the Outcome filter.'));return}
 const table=document.createElement('table'),head=document.createElement('thead'),headers=document.createElement('tr');
 for(const label of ['Frozen species','A wrong / classified','B wrong / classified','New errors'])headers.append(text('th',label));head.append(headers);table.append(head);
 const body=document.createElement('tbody');
 for(const row of result.ranking.slice(0,rankingExpanded?undefined:6)){const tr=document.createElement('tr'),name=document.createElement('td'),button=text('button',row.species);
 function choose(status){if(dirty.size){message('Save pending corrections first.',true);return}$('species').value=row.species;$('scope').value='truth';$('outcome').value=status;load(true)}
 button.setAttribute('aria-pressed',String(result.selected_species===row.species));button.onclick=()=>choose('b-mismatch');name.append(button);tr.append(name);
 for(const model of ['a','b'])tr.append(text('td',`${row[model+'_wrong']} / ${row[model+'_classified']}`));
 const changes=document.createElement('td');if(row.regressions){const b=text('button',String(row.regressions));b.setAttribute('aria-label',`${row.species}: ${row.regressions} new errors`);b.onclick=()=>choose('taxonomy-regressed');changes.append(b)}else changes.textContent='0';tr.append(changes);body.append(tr)}
 table.append(body);host.append(table);if(result.ranking.length>6){const expand=text('button',rankingExpanded?'Show top 6':`Show all ${result.ranking.length} species`);expand.onclick=()=>{rankingExpanded=!rankingExpanded;renderRanking()};host.append(expand)}
}
function render(){if(!result)return;currentPage=result.page;$('cards').replaceChildren();
 renderRanking();
 $('prev').disabled=currentPage<=0;$('next').disabled=currentPage+1>=result.pages;
 $('comparison-note').textContent=`A: ${result.labelA} · B: ${result.labelB}. ${result.basis==='center'?'CPU diagnostic sample at frozen labeled centers; unsampled targets are unavailable.':'Both models matched to B’s frozen labels on shared tiles.'} Current edits are separate; test is excluded.`;
 $('page-label').textContent=`Page ${currentPage+1} / ${result.pages} · ${result.total} examples`;
 $('species-list').replaceChildren(...result.species.map(s=>new Option(s,s)));
 for(const row of result.rows){const card=text('article','','card');const head=text('header','');
 head.append(text('strong',`${row.city_label||row.city} · ${row.tree_id||'Unmatched prediction'}`),text('span',row.status,'badge '+row.status));card.append(head);
 const crop=text('div','','crop'),img=document.createElement('img');img.loading='lazy';img.alt=`Imagery around ${row.tree_id||row.chip}`;
 const params=new URLSearchParams({city:row.city,image_run:row.image_run,chip:row.chip,x:row.x,y:row.y,span:$('span').value});
 img.src='/api/species-debugger/crop?'+params;img.onerror=()=>{img.alt='Crop unavailable; open tile to inspect';};crop.append(img);
 const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 320 320');marker(svg,160,160,'#ffeb55',8);
 const unit=row.scale/Number($('span').value)*320,border=document.createElementNS('http://www.w3.org/2000/svg','rect');
 for(const[k,v]of Object.entries({x:160-row.x*unit,y:160-row.y*unit,width:row.output_pixels*unit,height:row.output_pixels*unit,fill:'none',stroke:'#ffeb55','stroke-width':1.5,'stroke-dasharray':'5 5'}))border.setAttribute(k,v);svg.append(border);
 for(const [p,color,r]of [[row.a,'#38f58a',5],[row.b,'#5ac9ff',11]])if(p)marker(svg,160+(p.x-row.x)*row.scale/Number($('span').value)*320,160+(p.y-row.y)*row.scale/Number($('span').value)*320,color,r);
 crop.append(svg);card.append(crop);const info=text('div','','info'),dl=document.createElement('dl');
 const center=result.basis==='center';for(const[label,value]of [['Frozen GT',row.truth||'Unknown'],['Model A',prediction(center?row.center_a:row.a)],['Model B',prediction(center?row.center_b:row.b)]]){dl.append(text('dt',label),text('dd',value))}
 info.append(dl,text('div',`${row.chip} · center (${row.x}, ${row.y})`,'muted'));
 if(center)info.append(text('p','Classification sampled at the labeled center; detection confidence does not filter these classifications.','muted'));
 if(row.kind==='prediction')info.append(text('p','Unmatched against frozen labels; may be an unlabeled tree, not necessarily a false positive.','muted'));
 const link=text('a','Open full tile');link.target='_blank';link.rel='noopener';link.href=(result.split==='train'?'/curate-training?':'/curate?')+new URLSearchParams({city:row.city,run:row.image_run,chip:row.chip,threshold:result.threshold,return:'/species-debugger?'+query()});info.append(link);card.append(info);
 if(row.tree_id){const editor=text('div','','editor'),button=text('button','Edit current label');editor.append(button);card.append(editor);button.onclick=()=>editLabel(editor,row)}
 $('cards').append(card)}
}
async function editLabel(host,row){host.replaceChildren(text('div','Loading current saved label…'));const params=new URLSearchParams({city:row.city,chip:row.chip,tree:row.tree_id,image_run:row.image_run});
 try{let target=await json('/api/species-debugger/prepare?'+params,{method:'POST',headers:{'X-Studio-Species':'1'}});host.replaceChildren();
 const current=text('p',`Current saved label: ${target.current_species||'Unknown'}`),label=text('label','Scientific species or genus (blank = unknown)'),input=document.createElement('input');input.value=target.current_species;input.setAttribute('list','species-list');input.setAttribute('aria-label','Corrected species for '+row.tree_id);label.append(input);
 const actions=text('div','','actions'),save=text('button','Save correction'),reset=text('button','Use inventory label'),reload=text('button','Reload saved label');reset.hidden=Boolean(target.region);const cancel=text('button','Discard edit');actions.append(save,reset,reload,cancel);cancel.onclick=()=>{dirty.delete(row.id);lockControls();editLabel(host,row)};
 const msg=text('div','','message');host.append(current,label,actions,msg,text('small','Saved separately from frozen scores. Publish All before the next training run. Names outside its taxonomy remain masked for species training until the taxonomy includes them.','muted'));
 input.oninput=()=>{dirty.add(row.id);lockControls()};
 async function refresh(){target=await json('/api/species-debugger/label?'+params);current.textContent=`Current saved label: ${target.current_species||'Unknown'}`;}
 reload.onclick=async()=>{try{await refresh();msg.textContent='Reloaded saved state; your proposed correction remains in the input.';msg.classList.remove('error')}catch(e){msg.textContent=e.message;msg.classList.add('error')}};
 async function persist(resetLabel){save.disabled=reset.disabled=reload.disabled=cancel.disabled=true;msg.textContent='Saving…';
  try{target=await json('/api/species-debugger/label?'+params,{method:'PUT',headers:{'Content-Type':'application/json','X-Studio-Species':'1'},body:JSON.stringify({base_revision:target.base_revision,species:input.value,reset:resetLabel})});
   current.textContent=`Current saved label: ${target.current_species||'Unknown'}`;input.value=target.current_species;dirty.delete(row.id);lockControls();msg.textContent='Saved. Frozen predictions and scores are unchanged.';msg.classList.remove('error');
  }catch(e){msg.textContent=e.message;msg.classList.add('error')}finally{save.disabled=reset.disabled=reload.disabled=cancel.disabled=false}}
 save.onclick=()=>persist(false);reset.onclick=()=>persist(true);
 }catch(e){host.replaceChildren(text('p',e.message,'error'));const retry=text('button','Retry');retry.onclick=()=>editLabel(host,row);host.append(retry)}
}
async function load(resetPage=false,autoPick=false){if(dirty.size){message('Save your pending corrections before changing this view.',true);return}
 if(resetPage)currentPage=0;const token=++generation;clearTimeout(timer);$('load').disabled=true;
 const params=query(),labelA=$('model-a').selectedOptions[0]?.textContent,labelB=$('model-b').selectedOptions[0]?.textContent;if(autoPick)params.set('auto','1');result=null;$('ranking').hidden=true;$('cards').replaceChildren();$('page-label').textContent='';$('prev').disabled=$('next').disabled=true;history.replaceState(null,'','/species-debugger?'+params);message('Finding species mismatches…');
 $('comparison-note').textContent=$('basis').value==='center'?'Diagnostic CPU sample: classifications at the same frozen labeled centers. Rejected checkpoints are not promoted. Current edits remain separate.':'Both models use Model B’s frozen labels on shared tiles, with identical matching rules. Current edits remain separate. Test is excluded.';
 async function poll(){try{const d=await json('/api/species-debugger?'+params);if(token!==generation)return;
  if(d.state==='running'){message(d.stage||'Matching saved predictions…');timer=setTimeout(poll,2000);return}
  if(d.state==='error')throw Error(d.error);result=d;result.labelA=labelA;result.labelB=labelB;
  $('species').value=d.selected_species;$('outcome').value=d.selected_status;$('scope').value=d.selected_scope;
  params.delete('auto');params.set('species',d.selected_species);params.set('status',d.selected_status);params.set('scope',d.selected_scope);history.replaceState(null,'','/species-debugger?'+params);
  render();const coverage=(d.cities||[]).map(c=>`${c.label}: ${c.chips} tiles`).join(' · '),excluded=(d.excluded_cities||[]).map(c=>`${c.city.toUpperCase()}: ${c.reason}`).join('; ');message(`${d.total} examples across ${d.chips} shared tiles. ${coverage}. Showing up to 24 at a time.${excluded?' Excluded: '+excluded:''}`);$('load').disabled=false;
 }catch(e){if(token===generation){message(e.message,true);$('load').disabled=false}}}poll();
}
for(const[id,key]of [['split','split'],['basis','basis'],['species','species'],['scope','scope'],['outcome','status'],['threshold','threshold'],['policy','policy']])if(q.has(key))$(id).value=q.get(key);
$('load').onclick=()=>load(true);$('prev').onclick=()=>{if(!dirty.size){currentPage--;load()}};$('next').onclick=()=>{if(!dirty.size){currentPage++;load()}};
$('span').onchange=()=>{if(dirty.size)message('Save pending corrections before changing the crop size.',true);else render()};
$('model-a').onchange=syncBasis;$('model-b').onchange=syncBasis;
$('split').onchange=()=>{if(dirty.size){message('Save pending corrections before changing the split.',true);return}generation++;clearTimeout(timer);result=null;$('cards').replaceChildren();$('page-label').textContent='';loadOptions()};
loadOptions();
})();</script></body></html>'''
