"""Shared freehand curation areas, separate from radius/size annotations."""
SINGLE_TREE_JS = r'''
const polygonAreaModes=['single-tree','confirmed-woods','confirmed-background','copse-polygon','shrub-mass-polygon'];
function isVegetationGroup(mode){return ['copse','shrub-mass'].includes(mode?.replace('-polygon',''));}
const areaLabels={'single-tree':'Single-tree area','confirmed-woods':'Confirmed woods','confirmed-background':'Confirmed background'};
const areaDescriptions={'single-tree':'Extra centers penalized; known trees retained',
 'confirmed-woods':'Unmatched predictions ignored, not counted as correct; known trees retained',
 'confirmed-background':'Trusted negative supervision; known trees retained'};
for(const mode of ['copse','shrub-mass']){
 areaLabels[mode]=mode==='copse'?'Copse':'Shrub mass';
 areaDescriptions[mode]='One vegetation target replaces enclosed tree centers; extra detections inside ignored. Removing this group restores the tree labels';
 areaLabels[mode+'-polygon']=areaLabels[mode]+' polygon';areaDescriptions[mode+'-polygon']=areaDescriptions[mode];
}
function vegetationCenter(points){
 const mean=points.reduce((a,p)=>({x:a.x+p.x/points.length,y:a.y+p.y/points.length}),{x:0,y:0});
 if(singleAreaContains(mean,points))return mean;
 const levels=[...new Set(points.map(p=>p.y))].sort((a,b)=>a-b);let best=null;
 for(let j=1;j<levels.length;j++){
  const y=(levels[j-1]+levels[j])/2,xs=[];
  points.forEach((a,i)=>{const b=points[(i+1)%points.length];if((a.y>y)!==(b.y>y))xs.push(a.x+(y-a.y)*(b.x-a.x)/(b.y-a.y));});
  xs.sort((a,b)=>a-b);
  for(let i=0;i+1<xs.length;i+=2){const width=xs[i+1]-xs[i];if(!best||width>best.width)best={x:(xs[i]+xs[i+1])/2,y,width};}
 }
 if(!best)throw Error('Polygon has no interior');return best;
}
function pointInVegetationGroup(point,scene,excludeId=null){
 return maskRegions.some(r=>{
  if(!isVegetationGroup(r.mode)||r.region_id===excludeId)return false;
  const shown=regionForScene(r,scene);if(!shown)return false;
  if(r.world_polygon)return singleAreaContains(point,singleAreaPixels(r,scene));
  const anchor=samplesById[shown.anchor_sample_id];
  return Math.hypot(point.x-shown.image_x,point.y-shown.image_y)<=r.radius_m/regionPixelScaleM(anchor);
 });
}
function isPolygonAreaMode(mode){return polygonAreaModes.includes(mode);}
function translateAreaPolygon(region,east,north){
 if(region.world_polygon){
  region.world_polygon=region.world_polygon.map(([x,y])=>[x+east,y+north]);
  delete region.polygon;
 }
}
function singleAreaContains(point, points){
 let inside=false;
 for(let i=0,j=points.length-1;i<points.length;j=i++){
  const a=points[i],b=points[j];
  if((a.y>point.y)!==(b.y>point.y)&&point.x<(b.x-a.x)*(point.y-a.y)/(b.y-a.y)+a.x)inside=!inside;
 }
 return inside;
}
function singleAreaPixels(region,scene){
 if(region.points)return region.points;
 const anchor=samplesById[scene.sample_ids[0]||scene.anchor?.sample_id];
 if(!anchor||!region.world_polygon)return [];
 const det=anchor.transform_a*anchor.transform_e-anchor.transform_b*anchor.transform_d;
 return region.world_polygon.map(([x,y])=>({
  x:anchor.target_x+(anchor.transform_e*(x-anchor.world_x)-anchor.transform_b*(y-anchor.world_y))/det,
  y:anchor.target_y+(-anchor.transform_d*(x-anchor.world_x)+anchor.transform_a*(y-anchor.world_y))/det}));
}
function renderSingleArea(wrap,scene,region,draft){
 const selected=selectedAddedByScene[scene.scene_id]===region.region_id;
 const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');
 svg.setAttribute('viewBox',`0 0 ${scene.image_width} ${scene.image_height}`);
 svg.classList.add('single-tree-area');svg.dataset.regionId=region.region_id||'draft';
 svg.classList.toggle('draft',Boolean(draft));
 Object.assign(svg.style,{position:'absolute',inset:'0',width:'100%',height:'100%',pointerEvents:'none',zIndex:'7',overflow:'visible'});
 const poly=document.createElementNS(svg.namespaceURI,'polygon');
 poly.setAttribute('points',singleAreaPixels(region,scene).map(p=>`${p.x},${p.y}`).join(' '));
 const color={'single-tree':'#ffbd45','confirmed-woods':'#55ddee','confirmed-background':'#ff4055','copse':'#c89bff','shrub-mass':'#f5a75c','copse-polygon':'#c89bff','shrub-mass-polygon':'#f5a75c'}[region.mode];
 poly.setAttribute('fill',color+'1a');poly.setAttribute('stroke',selected?'#ffffff':color);
 poly.setAttribute('stroke-width','2');poly.setAttribute('vector-effect','non-scaling-stroke');
 if(draft)poly.setAttribute('stroke-dasharray','5 4');
 // Only the outline is clickable; the enclosed trees remain selectable.
 if(!draft){poly.style.pointerEvents='stroke';poly.style.cursor='pointer';
  const title=document.createElementNS(svg.namespaceURI,'title');
  title.textContent=areaLabels[region.mode]+': '+areaDescriptions[region.mode]+'. Select outline, then drag or WASD to move. Shift-click outline to remove.';poly.append(title);
  const card=wrap.closest('.card'),anchor=samplesById[scene.sample_ids[0]||scene.anchor?.sample_id];
  bindCrownDrag(poly,card,scene,region,anchor);
  poly.addEventListener('click',e=>{e.preventDefault();e.stopPropagation();
   if(regionModeByScene[scene.scene_id])return;
   if(e.shiftKey){removeMaskRegion(region.region_id);return;}
   deselectedScenes.delete(scene.scene_id);selectStandaloneRegion(scene.scene_id,region.region_id);update(scene.scene_id);
  });
 }
 svg.append(poly);wrap.append(svg);
}
function confirmTileBackground(sceneId){
 const scene=scenesById[sceneId];
 if(metadata.curation_schema_version!==2||!scene)return;
 const anchor=samplesById[scene.sample_ids[0]||scene.anchor?.sample_id];
 if(!anchor){alert('This tile has no geographic anchor.');return;}
 const width=scene.image_width,height=scene.image_height;
 if(!(width>0&&height>0))return;
 // Tile coordinates exclude the surrounding context border and current zoom.
 const points=[{x:0,y:0},{x:width,y:0},{x:width,y:height},{x:0,y:height}];
 const existing=maskRegions.some(r=>r.mode==='confirmed-background'&&r.world_polygon&&(()=>{
  const pixels=singleAreaPixels(r,scene);
  return pixels?.length===4&&pixels.every((p,i)=>Math.abs(p.x-points[i].x)<1e-6&&Math.abs(p.y-points[i].y)<1e-6);
 })());
 if(existing){alert('This tile already has a full-tile background area.');return;}
 finishSingleArea(sceneId,{mode:'confirmed-background',points});
}
function finishSingleArea(sceneId,draft){
 const scene=scenesById[sceneId],anchor=samplesById[scene.sample_ids[0]||scene.anchor?.sample_id],points=draft.points,mode=(draft.mode||'single-tree').replace('-polygon','');
 delete regionDraftByScene[sceneId];regionModeByScene[sceneId]=null;
 const area=Math.abs(points.reduce((n,p,i)=>{const q=points[(i+1)%points.length];return n+p.x*q.y-p.y*q.x},0))/2;
 const labeled=scene.sample_ids.some(id=>!['not-tree','uncertain','duplicate','occluded'].includes(statusOf(id))&&singleAreaContains(displayedPosition(samplesById[id]),points))
  ||maskRegions.some(r=>r.mode==='confirmed-tree'&&!r.tree_id&&(()=>{const p=regionForScene(r,scene);return p&&singleAreaContains({x:p.image_x,y:p.image_y},points)})());
 if(points.length<3||area<4){alert('Trace a closed area with at least three points.');update(sceneId);return;}
 if(mode==='single-tree'&&!labeled){alert('Trace a canopy containing at least one retained tree center. Add or align its tree first.');update(sceneId);return;}
 const world=points.map(p=>{const dx=p.x-anchor.target_x,dy=p.y-anchor.target_y;return [anchor.world_x+anchor.transform_a*dx+anchor.transform_b*dy,anchor.world_y+anchor.transform_d*dx+anchor.transform_e*dy]});
 const interior=isVegetationGroup(mode)?vegetationCenter(world.map(([x,y])=>({x,y}))):null;
 const center=interior?[interior.x,interior.y]:world.reduce((a,p)=>[a[0]+p[0]/world.length,a[1]+p[1]/world.length],[0,0]);
 const radius=Math.max(...world.map(p=>Math.hypot(p[0]-center[0],p[1]-center[1])))+.001;
 if(radius>500){alert('Areas must fit within 500 m.');update(sceneId);return;}
 maskRegions.push({region_id:crypto.randomUUID(),mode,world_x:center[0],world_y:center[1],world_polygon:world,radius_m:radius,source:'human',created_at:new Date().toISOString()});
 persist(sceneId);
}
'''
