"""Frozen-label match indicators shared by both curation prediction overlays."""

PREDICTION_MATCH_JS = r'''
function curationMatchRadius() {
  const value=Number(pageParameters.get('radius'));
  return Number.isFinite(value)&&value>0?value:2;
}
function frozenPredictionMatches(data, display, threshold, radiusM) {
  const scale=Number(display?.resolution_m)*Number(display?.output_stride);
  const predictions=(data.predictions||[]).filter(p=>Number.isFinite(Number(p.score))&&Number(p.score)>=threshold)
    .slice().sort((a,b)=>Number(b.score)-Number(a.score));
  const results=new Map(),used=new Set();
  const available=!data.prediction_only&&Array.isArray(data.ground_truth)&&Number.isFinite(scale)&&scale>0&&Number.isFinite(radiusM)&&radiusM>0;
  for(const p of predictions){
    if(!available){results.set(p,{status:'unavailable'});continue;}
    let best=-1,distance=Infinity;
    data.ground_truth.forEach((tree,i)=>{
      if(used.has(i)||[p.output_x,p.output_y,tree.output_x,tree.output_y].some(v=>v==null||!Number.isFinite(Number(v))))return;
      const d=Math.hypot(Number(p.output_x)-Number(tree.output_x),Number(p.output_y)-Number(tree.output_y));
      if(d<=radiusM/scale&&d<distance){best=i;distance=d;}
    });
    if(best>=0){used.add(best);results.set(p,{status:'matched',tree:data.ground_truth[best],distanceM:distance*scale});}
    else {
      const mask=p.detection_mask_value;
      results.set(p,{status:mask!=null&&Number.isFinite(Number(mask))&&Number(mask)<=0?'ignored':'unmatched'});
    }
  }
  return results;
}
function decoratePredictionMatch(marker, match, radiusM) {
  const status=match?.status||'unavailable';
  marker.classList.add('prediction-match-'+status);
  const detail=status==='matched'?`Matched to ${match.tree.tree_id??'frozen tree'} · ${match.distanceM.toFixed(2)} m away`
    :status==='ignored'?'Unmatched — ignored by scoring'
    :status==='unmatched'?'Unmatched — counted as an extra prediction'
    :'Match status unavailable';
  marker.title=(marker.title?marker.title+' · ':'')+detail+` · Frozen evaluation labels, ${radiusM} m matching radius; not live curation`;
  marker.setAttribute('aria-label',marker.title);
}
function enablePredictionMatchHover(card) {
  const wrap=card.querySelector('.image-wrap');
  if(wrap._predictionMatchHover)return;
  wrap._predictionMatchHover=true;
  // Delegate hover without making comparison overlays intercept ordinary edits.
  wrap.addEventListener('pointermove',event=>{
    if(card.classList.contains('isolate-selection')||wrap.classList.contains('region-drawing')){wrap.removeAttribute('title');return;}
    const selector=card.classList.contains('comparing-models')
      ?'.curation-comparison-ring.accept-prediction-center':'.prediction-marker';
    const box=wrap.getBoundingClientRect();let nearest=null,distance=10;
    for(const marker of wrap.querySelectorAll(selector)){
      const x=box.left+parseFloat(marker.style.left)*box.width/100,y=box.top+parseFloat(marker.style.top)*box.height/100;
      const d=Math.hypot(event.clientX-x,event.clientY-y);
      if(d<=distance){nearest=marker;distance=d;}
    }
    if(nearest)wrap.title=nearest.title;else wrap.removeAttribute('title');
  },{passive:true});
  wrap.addEventListener('pointerleave',()=>wrap.removeAttribute('title'));
}
'''
