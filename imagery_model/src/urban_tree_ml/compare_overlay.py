"""Two-model, identity-colored overlay for the existing chip comparison page."""

COMPARE_OVERLAY = r"""
<style>
.overlay-card{margin:20px;max-width:1100px}.overlay-card .image{width:100%;height:auto;aspect-ratio:1}.overlay-controls{padding:12px;display:flex;flex-wrap:wrap;gap:12px;align-items:center}.overlay-controls label{display:flex;gap:6px;align-items:center}.overlay-controls select{max-width:360px}.overlay-controls input[type=checkbox]{min-height:initial}.overlay-a,.overlay-b{position:absolute;display:block;transform:translate(-50%,-50%);border-radius:50%;background:transparent!important;padding:0!important;min-width:0!important;min-height:0!important;box-sizing:border-box;z-index:5}.overlay-a{width:17px!important;height:17px!important;border:2px solid rgba(183,154,255,0.65)!important}.overlay-b{width:11px!important;height:11px!important;border:2px solid #ffae39!important;z-index:6}.overlay-truth{position:absolute;width:4px;height:4px;border-radius:50%;background:white;transform:translate(-50%,-50%);z-index:4}.overlay-legend-a{color:#b79aff}.overlay-legend-b{color:#ffae39}.overlay-card:fullscreen{max-width:none;margin:0;overflow:auto;background:#0d1410;padding:10px;display:flex;flex-direction:column;align-items:center}.overlay-card:fullscreen .image{width:min(85vw,calc(100vh - 150px));flex-shrink:0}
</style>
<section id="overlay-card" class="card overlay-card" hidden>
<div class="card-head"><strong>Two-model overlay — colors identify runs, not correctness</strong><button id="overlay-fullscreen" type="button">Full screen</button></div>
<div class="overlay-controls">
<label class="overlay-legend-a"><input id="show-overlay-a" type="checkbox" checked> Purple / outer ring <select id="overlay-a" aria-label="Purple model"></select></label>
<label class="overlay-legend-b"><input id="show-overlay-b" type="checkbox" checked> Orange / inner ring <select id="overlay-b" aria-label="Orange model"></select></label>
<label><input id="overlay-truth" type="checkbox"> White dots: orange model’s labels</label>
</div><div id="overlay-image" class="image"></div><div id="overlay-note" class="facts"></div></section>
<script>
document.querySelector('#form').insertAdjacentHTML('beforeend','<label>Shared confidence <input id="compare-threshold" type="number" min="0" max="1" step="0.05" value="0.35" style="width:90px"></label>');
$('grid').before($('overlay-card'));
function overlayThreshold(){const n=Number($('compare-threshold').value);return Number.isFinite(n)?Math.max(0,Math.min(1,n)):.35}
function overlayCompatible(a,b){return ['chip_pixels','output_stride','resolution_m'].every(k=>a.display[k]===b.display[k])}
function overlayMarkers(entry,kind,threshold){const scale=100*entry.display.output_stride/entry.display.chip_pixels;return entry.data.predictions.filter(p=>p.score>=threshold).map(p=>`<i class="overlay-${kind}" style="left:${p.output_x*scale}%;top:${p.output_y*scale}%" title="${esc(entry.run.training_run_id||entry.run.run_id)} · ${esc(p.species||'Unknown species')} · ${(p.score*100).toFixed(1)}% confidence"></i>`).join('')}
function renderOverlay(){
 const available=payload.runs.filter(e=>e.available);$('overlay-card').hidden=available.length<2;if(available.length<2)return;
 for(const id of ['overlay-a','overlay-b']){const select=$(id),previous=select.value;select.innerHTML=available.map(e=>`<option value="${esc(e.run.run_id)}">${esc(e.run.training_run_id||e.run.run_id)}</option>`).join('');if(available.some(e=>e.run.run_id===previous))select.value=previous;else if(id==='overlay-b')select.value=available.find(e=>e.run.run_id===payload.selected_run_id)?.run.run_id||available[0].run.run_id;else select.value=available.find(e=>(e.run.training_run_id||e.run.run_id)==='sf-boston-naip-curation-v5-retrain')?.run.run_id||available.find(e=>e.run.run_id!==payload.selected_run_id)?.run.run_id||available[0].run.run_id;}
 const a=available.find(e=>e.run.run_id===$('overlay-a').value),b=available.find(e=>e.run.run_id===$('overlay-b').value),threshold=overlayThreshold();
 if(!overlayCompatible(a,b)){$('overlay-image').innerHTML='<div class="empty">Different image scales: these runs cannot be overlaid safely.</div>';$('overlay-note').textContent='';return;}
 const scale=100*b.display.output_stride/b.display.chip_pixels;
 $('overlay-image').innerHTML=`<img alt="Shared imagery with two model prediction layers" src="/api/model/image/${encodeURIComponent(payload.chip_id)}.png?run=${encodeURIComponent(b.run.run_id)}">`+($('show-overlay-a').checked?overlayMarkers(a,'a',threshold):'')+($('show-overlay-b').checked?overlayMarkers(b,'b',threshold):'')+($('overlay-truth').checked?b.data.ground_truth.map(t=>`<i class="overlay-truth" title="Orange model label: ${esc(t.species||t.tree_id)}" style="left:${t.output_x*scale}%;top:${t.output_y*scale}%"></i>`).join(''):'');
 const count=e=>e.data.predictions.filter(p=>p.score>=threshold).length;
 $('overlay-note').textContent=`${payload.chip_id} · Purple: ${count(a)} predictions · Orange: ${count(b)} predictions · confidence ≥ ${threshold.toFixed(2)}. Coincident predictions show nested rings; hover for run/species/confidence. Position proximity alone is not a scored match.`;
}
const renderComparisonCards=render;render=function(){renderComparisonCards();if(payload)renderOverlay()};
for(const id of ['overlay-a','overlay-b','show-overlay-a','show-overlay-b','overlay-truth'])$(id).addEventListener('change',renderOverlay);
$('compare-threshold').addEventListener('change',render);
$('overlay-fullscreen').addEventListener('click',async()=>{try{if(document.fullscreenElement)await document.exitFullscreen();else await $('overlay-card').requestFullscreen()}catch(e){$('overlay-note').textContent='Full screen unavailable; the overlay remains usable here.'}});
</script>
"""
