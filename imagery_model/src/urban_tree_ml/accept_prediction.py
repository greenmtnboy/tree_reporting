"""Explicit Alt-click acceptance into the existing georeferenced crown workflow."""

ACCEPT_PREDICTION_JS = r'''
function predictionSpeciesSuggestion(prediction) {
  const name=typeof prediction.species==='string'?prediction.species.trim():'';
  const usable=name && !/^(unknown(?: species)?|other|unclassified|none|null)$/i.test(name);
  const confidence=prediction.species_confidence;
  const valid=confidence!=null && Number.isFinite(Number(confidence)) && Number(confidence)>=0 && Number(confidence)<=1;
  return {species:usable?name:'', confidence:valid?Number(confidence):null};
}
function acceptPrediction(card, scene, prediction, display, confirmSpecies=false, run='') {
  if (cards.inert || regionModeByScene[scene.scene_id]) return;
  const anchor = samplesById[activeByScene[scene.scene_id] || scene.sample_ids[0]];
  const fractionX = Number(prediction.output_x) * Number(display.output_stride) / Number(display.chip_pixels);
  const fractionY = Number(prediction.output_y) * Number(display.output_stride) / Number(display.chip_pixels);
  const radius = Number(prediction.crown_radius_m);
  const note = card.querySelector('.prediction-badge');
  if (['copse','shrub-mass'].includes(prediction.vegetation_type)) {
    if(note)note.textContent='Use Group circle or Group polygon to review this vegetation group; it is not an individual tree.';
    return;
  }
  const suggestion=predictionSpeciesSuggestion(prediction);
  const template=confirmSpecies && suggestion.species ? {
    species:suggestion.species,
    identification_source:`Human confirmed model suggestion via Alt+Shift-click${run?' · '+run:''}${suggestion.confidence!=null?' · species confidence '+(100*suggestion.confidence).toFixed(0)+'%':''}`
  }:null;
  if (!anchor || ![fractionX,fractionY].every(v=>Number.isFinite(v)&&v>=0&&v<=1)) return;
  if (!Number.isFinite(radius) || radius <= 0 || radius > 500) {
    if(note)note.textContent='No usable predicted crown radius. Use Add tree crown (T) to draw this tree.';
    return;
  }
  const x=fractionX*scene.image_width, y=fractionY*scene.image_height;
  const existing=maskRegions.map(r=>regionForScene(r,scene)).find(r=>r && r.mode==='confirmed-tree'
    && !r.tree_id && Math.hypot(r.image_x-x,r.image_y-y)<1);
  if(existing){
    selectedAddedByScene[scene.scene_id]=existing.region_id;
    deselectedScenes.delete(scene.scene_id);
    const stored=maskRegions.find(r=>r.region_id===existing.region_id);
    if(template && !stored.species){Object.assign(stored,template);persist(scene.scene_id);}
    else update(scene.scene_id);
    if(note)note.textContent=template && stored.species===template.species
      ? 'Selected added tree · species confirmed: '+stored.species
      : 'This center already has an added tree; selected it. Existing species was not changed.';
    return;
  }
  // Species requires the extra explicit modifier; never accept predicted DBH.
  regionModeByScene[scene.scene_id]='confirmed-tree';
  if(template)finishMaskRegion(scene.scene_id,x,y,radius/regionPixelScaleM(anchor),null,template);
  else finishMaskRegion(scene.scene_id,x,y,radius/regionPixelScaleM(anchor));
  if(confirmSpecies && !template && note)note.textContent='Added crown only: no usable species suggestion.';
}
function bindPredictionAcceptance(marker,card,scene,prediction,display,run=''){
  marker.classList.add('accept-prediction-center');
  const suggestion=predictionSpeciesSuggestion(prediction);
  const label=suggestion.species?`${suggestion.species} · ${suggestion.confidence==null?'species confidence unavailable':(100*suggestion.confidence).toFixed(0)+'% species confidence'}`:'Species suggestion unavailable';
  marker.title=(marker.title?marker.title+' · ':'')+label+' · Alt-click: add crown only. Alt+Shift-click: also confirm suggested species. DBH remains unknown.';
  marker.addEventListener('pointerdown',event=>{
    if(event.altKey){event.preventDefault();event.stopPropagation();}
  });
  marker.addEventListener('click',event=>{
    if(!event.altKey)return;
    event.preventDefault();event.stopImmediatePropagation();
    acceptPrediction(card,scene,prediction,display,event.shiftKey,run);
  },true);
}
document.head.insertAdjacentHTML('beforeend',`<style>
body.accept-predictions .card.fullscreen .accept-prediction-center{pointer-events:auto!important;cursor:copy!important;z-index:80!important}
</style>`);
document.addEventListener('keydown',event=>{
  if(event.key==='Alt' && !event.target.closest('input,textarea,select,[contenteditable="true"]')
      && document.querySelector('.card.fullscreen')){
    event.preventDefault();document.body.classList.add('accept-predictions');
  }
});
document.addEventListener('keyup',event=>{
  if(event.key==='Alt'){document.body.classList.remove('accept-predictions');}
});
window.addEventListener('blur',()=>document.body.classList.remove('accept-predictions'));
'''
