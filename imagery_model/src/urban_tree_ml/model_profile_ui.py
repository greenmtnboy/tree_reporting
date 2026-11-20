"""Read-only model description tab, loaded independently of chip scoring."""
PROFILE_HTML = r'''<!doctype html><html><head><meta charset="utf-8"><title>Model description</title>
<style>body{background:#101b17;color:#dfebe2;font:15px system-ui;margin:0}main{max-width:1200px;margin:auto;padding:24px}h2{margin-top:30px}.controls{display:flex;gap:12px;flex-wrap:wrap}select,button{background:#21382d;color:#eef6ee;border:1px solid #658373;padding:9px}table{border-collapse:collapse;width:100%}td,th{padding:10px;text-align:left;border-bottom:1px solid #385546}.scroll{overflow:auto}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#1b3025;padding:16px}.muted{color:#a7bbaa}.status{padding:22px;border:1px solid #7dd4ac;margin:18px 0}section[hidden]{display:none}a{color:#9dddcb}</style></head><body><main>
<h1>Model description</h1><p>Actual saved training recipe and selected-checkpoint evidence. Read-only; changing the model here does not promote it or change annotations.</p>
<div class="controls"><button id="profile-reload">Reload profile</button><a id="profile-download">Download profile JSON</a></div>
<div id="profile-status" class="status" role="status" aria-live="polite">Loading model profile…</div>
<section id="profile-content" hidden>
<h2>Identity and checkpoint selection</h2><dl id="profile-identity"></dl>
<h2>Validation-loss objective</h2><p id="profile-loss-source"></p><p class="muted">Weighted contribution = coefficient × raw loss. Value shares are not gradient shares, and are not a percentage of model importance.</p>
<div class="scroll"><table><thead><tr><th>Component / scope</th><th>Coefficient</th><th>Raw loss</th><th>Weighted contribution</th><th>Share of total</th></tr></thead><tbody id="profile-losses"></tbody></table></div>
<h2>Architecture and training recipe</h2><dl id="profile-recipe"></dl>
<details><summary>Full recorded recipe and selection details</summary><pre id="profile-raw"></pre></details>
<h2>Development inference coverage</h2><p class="muted">Counts from saved evaluation artifacts, not live curation. Shifted training windows may repeat source trees.</p><div class="scroll"><table><thead><tr><th>Cohort</th><th>Split</th><th>Chips</th><th>Labeled trees</th></tr></thead><tbody id="profile-cohorts"></tbody></table></div>
<h2>Interpretation and provenance</h2><ul id="profile-caveats"></ul><details><summary>Source artifacts and frozen validation signature</summary><pre id="profile-sources"></pre></details>
</section></main><script>
(()=>{const el=id=>document.getElementById(id),num=v=>v==null?'—':Number(v).toPrecision(6);let generation=0;
const style=document.createElement('style');style.textContent='dl{display:grid;grid-template-columns:minmax(140px,200px) 1fr;gap:12px;background:#1b3025;padding:18px}dt{color:#a7bbaa}dd{margin:0;overflow-wrap:anywhere}summary{cursor:pointer;color:#9dddcb}';document.head.append(style);
function facts(id,rows){el(id).replaceChildren();for(const [key,value] of rows){const dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=key;dd.textContent=value??'Not recorded';el(id).append(dt,dd)}}
async function load(){const token=++generation;el('profile-content').hidden=true;el('profile-status').textContent='Loading model recipe and checkpoint evidence…';el('profile-reload').disabled=true;
const run=new URLSearchParams(location.search).get('run')||el('review-model')?.value||'';
const query=new URLSearchParams({run,city:'all'});el('profile-download').href='/api/model-profile?'+query;el('profile-download').download='model-profile.json';
try{const r=await fetch('/api/model-profile?'+query),p=await r.json();if(!r.ok)throw Error(p.error||'Profile load failed');if(token!==generation)return;
const selection=p.selection;
facts('profile-identity',[['Run',p.run],['Parent checkpoint',p.parent],['Parent SHA256',p.parent_sha256],['Automatic selected checkpoint',p.selected_checkpoint],['Explicitly promoted checkpoint',p.user_promotion?.checkpoint||'None'],['Promotion interpretation',p.user_promotion?'User override for next training parent. Existing cohort metrics still describe the automatic selected checkpoint.':'Automatic selection applies'],['Selected step',p.selected_step],['Parent baseline loss',num(p.baseline_loss)],['Selection',selection?.selected==='parent'?'Parent retained — trained checkpoints did not improve validation loss':selection?.selected==='trained'?'Trained checkpoint selected — lower validation loss': 'Historical or unverified run: no recorded parent-baseline comparison'],['Eligible as next parent',selection?String(selection.eligible_for_parent_advance):'Not established by this historical run']]);
el('profile-loss-source').textContent=p.loss_source+' · Total loss: '+num(p.loss_total);
el('profile-losses').replaceChildren();for(const row of p.loss_components){const tr=document.createElement('tr');for(const value of [row.label+' — '+row.description,num(row.weight),num(row.raw),num(row.weighted),row.share==null?'—':(100*row.share).toFixed(4)+'%']){const td=document.createElement('td');td.textContent=value;tr.append(td)}el('profile-losses').append(tr)}
const t=p.training,a=p.architecture;
facts('profile-recipe',[['Backbone',a.backbone],['Input channels',a.input_channels],['Chip width (pixels)',p.image_grid?.chip_pixels],['Resolution (m/pixel)',p.image_grid?.resolution_m],['Genus / species classes',(p.taxonomy_counts.genera??'—')+' / '+(p.taxonomy_counts.species??'—')],['Learning rate',t.learning_rate],['Batch size',t.batch_size],['Gradient accumulation',t.accumulate_grad_batches],['Precision',t.precision],['Augmentation',t.augmentation],['Stopping budget policy',t.budget_policy],['Patience (validation checks)',t.early_stopping_patience_checks],['Maximum passes / epochs',t.epochs],['Loss policy',p.loss_policy?.name||'Recorded coefficients above; no named policy']]);
el('profile-raw').textContent=JSON.stringify({architecture:a,training:t,budget:p.training_budget,loss_policy:p.loss_policy,selection},null,2);
el('profile-cohorts').replaceChildren();for(const row of (p.cohorts.length?p.cohorts:[{cohort:'Coverage unavailable'}])){const tr=document.createElement('tr');for(const key of ['cohort','split','chips','trees']){const td=document.createElement('td');td.textContent=row[key]??'—';tr.append(td)}el('profile-cohorts').append(tr)}
el('profile-caveats').replaceChildren();for(const text of p.caveats){const li=document.createElement('li');li.textContent=text;el('profile-caveats').append(li)}
el('profile-sources').textContent=JSON.stringify({signature:p.source_signature,validation_inputs:p.validation_inputs,sources:p.sources},null,2);
el('profile-content').hidden=false;el('profile-status').textContent='Loaded '+p.run+' · '+(p.complete?'Completed run':'Incomplete run; values may change');
}catch(e){if(token===generation)el('profile-status').textContent='Could not load model profile: '+e.message+' — use Reload profile to retry.'}finally{if(token===generation)el('profile-reload').disabled=false}}
el('profile-reload').onclick=load;document.addEventListener('DOMContentLoaded',load);
})();</script></body></html>'''
