"""Shared editable-tree view over inventory decisions and standalone crowns.

Selection and movement operate on this union. Only the adapters know which
canonical annotation store to update; no synthetic inventory labels are saved.
"""

EDITOR_TREES_JS = r'''
const treeSelectionByScene = {};
const addedTreeKey = id => 'added:'+id;
function editableTrees(scene){
 const trees=new Map();
 for(const id of scene.sample_ids){
  const sample=samplesById[id];
  if(!sample||sample.reference_only||isHiddenTree(sample)||(!showStacks.checked&&isStacked(sample)))continue;
  trees.set(id,{
   key:id, sampleId:id, position:()=>displayedPosition(sample),
   snapshot:()=>({value:JSON.parse(JSON.stringify(reviews[id]||{})),existed:Object.hasOwn(reviews,id)}),
   restore:s=>{if(s.existed)reviews[id]=s.value;else delete reviews[id];},
   move:(dx,dy)=>{
    const p=displayedPosition(sample),x=p.x+dx,y=p.y+dy;
    reviews[id]={...reviews[id],status:'offset',source:'human',image_x:x,image_y:y,
     east_m:sample.transform_a*(x-sample.target_x)+sample.transform_b*(y-sample.target_y),
     north_m:sample.transform_d*(x-sample.target_x)+sample.transform_e*(y-sample.target_y)};
    delete reviews[id].heuristic_id;
   },
   classify:status=>{
    const next={...reviews[id],status,source:'human'};delete next.heuristic_id;
    if(status!=='occluded')for(const k of ['image_x','image_y','east_m','north_m'])delete next[k];
    reviews[id]=next;
   }
  });
 }
 for(const region of maskRegions){
  if(region.mode!=='confirmed-tree'||region.tree_id||region.world_polygon)continue;
  const projected=regionForScene(region,scene);
  if(!projected||pointInVegetationGroup({x:projected.image_x,y:projected.image_y},scene))continue;
  const anchor=samplesById[projected.anchor_sample_id];if(!anchor)continue;
  const key=addedTreeKey(region.region_id);
  trees.set(key,{
   key,regionId:region.region_id,
   position:()=>{const p=regionForScene(region,scene);return {x:p.image_x,y:p.image_y};},
   snapshot:()=>({value:JSON.parse(JSON.stringify(region)),index:maskRegions.indexOf(region)}),
   restore:s=>{
    const existing=maskRegions.findIndex(r=>r.region_id===region.region_id);
    if(existing>=0)maskRegions[existing]=s.value;else maskRegions.splice(s.index,0,s.value);
   },
   move:(dx,dy)=>{
    const east=anchor.transform_a*dx+anchor.transform_b*dy,north=anchor.transform_d*dx+anchor.transform_e*dy;
    if(region.world_x!=null){region.world_x+=east;region.world_y+=north;delete region.longitude;delete region.latitude;}
    else {region.image_x+=dx;region.image_y+=dy;region.east_m=(region.east_m||0)+east;region.north_m=(region.north_m||0)+north;}
   },
   classify:status=>{
    // Preserve the existing standalone-tree semantics: exclusion removes it;
    // aligned is already implicit in a human-confirmed crown.
    if(['not-tree','duplicate','uncertain'].includes(status))maskRegions=maskRegions.filter(r=>r.region_id!==region.region_id);
   }
  });
 }
 return trees;
}
function selectedEditableTrees(sceneId){
 const scene=scenesById[sceneId];if(!scene||deselectedScenes.has(sceneId))return [];
 const trees=editableTrees(scene),keys=treeSelectionByScene[sceneId];
 if(keys)return [...keys].map(id=>trees.get(id)).filter(Boolean);
 const region=selectedAddedByScene[sceneId];
 return [...(region?[addedTreeKey(region)]:selectionFor(sceneId))].map(id=>trees.get(id)).filter(Boolean);
}
function applyTreeSelection(scene,keys){
 const trees=editableTrees(scene),selected=[...keys].map(id=>trees.get(id)).filter(Boolean);
 treeSelectionByScene[scene.scene_id]=new Set(selected.map(t=>t.key));
 selectedByScene[scene.scene_id]=new Set(selected.filter(t=>t.sampleId).map(t=>t.sampleId));
 delete selectedAddedByScene[scene.scene_id];
 if(selected.length){
  deselectedScenes.delete(scene.scene_id);
  const inventory=selected.find(t=>t.sampleId);if(inventory)activeByScene[scene.scene_id]=inventory.sampleId;
  if(selected.length===1&&selected[0].regionId)selectedAddedByScene[scene.scene_id]=selected[0].regionId;
  zoomFocusByScene[scene.scene_id]={...selected[0].position(),label:selected.length>1?'Selected trees':'Selected tree'};
 }else deselectedScenes.add(scene.scene_id);
}
function selectEditableTree(sceneId,key,extend=false){
 const selected=new Set(extend?selectedEditableTrees(sceneId).map(t=>t.key):[]);
 if(extend&&selected.has(key))selected.delete(key);else selected.add(key);
 applyTreeSelection(scenesById[sceneId],selected);update(sceneId);
}
function selectTreesInBox(scene,start,end){
 const keys=[...editableTrees(scene).values()].filter(tree=>{
  const {x,y}=tree.position(),epsilon=1e-4;
  return x>=Math.min(start.x,end.x)-epsilon&&x<=Math.max(start.x,end.x)+epsilon&&y>=Math.min(start.y,end.y)-epsilon&&y<=Math.max(start.y,end.y)+epsilon;
 }).map(tree=>tree.key);
 applyTreeSelection(scene,keys);
}
function rememberTreeEdit(sceneId,trees,kind,repeat=false){
 const key=kind+':'+trees.map(t=>t.key).sort().join('|'),stack=moveUndoByScene[sceneId]||[];
 if(repeat&&stack.at(-1)?.key===key)return;
 pushReviewUndo(sceneId,{kind:'editable-trees',key,trees:trees.map(t=>({tree:t,before:t.snapshot()})),selection:trees.map(t=>t.key)});
}
function undoTreeEdit(sceneId,saved){
 if(saved.kind!=='editable-trees')return false;
 for(const item of saved.trees)item.tree.restore(item.before);
 applyTreeSelection(scenesById[sceneId],saved.selection);persist(sceneId);return true;
}
function nudgeEditableTrees(scene,delta,step,repeat=false){
 const trees=selectedEditableTrees(scene.scene_id);if(!trees.length)return false;
 rememberTreeEdit(scene.scene_id,trees,'move',repeat);
 let dx=delta[0]*step,dy=delta[1]*step;
 for(const tree of trees){const p=tree.position();
  dx=Math.max(-editableBorder(scene)-p.x,Math.min(scene.image_width+editableBorder(scene)-1-p.x,dx));
  dy=Math.max(-editableBorder(scene)-p.y,Math.min(scene.image_height+editableBorder(scene)-1-p.y,dy));
 }
 for(const tree of trees)tree.move(dx,dy);
 zoomFocusByScene[scene.scene_id]={...trees[0].position(),label:'Nudged selection'};
 persist(scene.scene_id);return true;
}
function classifyEditableTrees(sceneId,status){
 const trees=selectedEditableTrees(sceneId);if(!trees.length)return false;
 rememberTreeEdit(sceneId,trees,'classification');
 for(const tree of trees)tree.classify(status);
 applyTreeSelection(scenesById[sceneId],trees.map(t=>t.key));persist(sceneId);return true;
}
function regionIsSelected(sceneId,id){
 return treeSelectionByScene[sceneId]?.has(addedTreeKey(id))||selectedAddedByScene[sceneId]===id;
}
function selectStandaloneRegion(sceneId,id){
 delete treeSelectionByScene[sceneId];selectedByScene[sceneId]=new Set();
 selectedAddedByScene[sceneId]=id;deselectedScenes.delete(sceneId);
}
'''
