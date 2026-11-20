"""Neighbor references and an explicit duplicate check for new crowns."""

BORDER_INVENTORY_JS = r'''
let borderInventory = {trees:[], available:false};
let highlightedNeighbor = null;
function renderBorderInventory(card,scene){
 const wrap=card.querySelector('.image-wrap');
 wrap.querySelectorAll('.inventory-context').forEach(el=>el.remove());
 if(!scene.contextReady)return;
 for(const tree of borderInventory.trees){
  if(['not-tree','duplicate'].includes(tree.status))continue;
  const marker=document.createElement('button');marker.type='button';
  marker.className='inventory-context inventory-context-marker';marker.dataset.treeId=tree.tree_id;
  marker.classList.toggle('inventory-context-highlighted',highlightedNeighbor===tree.tree_id);
  marker.textContent='I';marker.style.left=(100*tree.x/scene.image_width)+'%';marker.style.top=(100*tree.y/scene.image_height)+'%';
  const action=tree.owner_scene_id?'Open its original review tile':'Inventory reference; its review tile is not prepared';
  marker.title=`Neighbor inventory: ${tree.species||'Unknown'} · ${tree.tree_id} · ${tree.status||'unreviewed'} · ${action}`;
  marker.setAttribute('aria-label',marker.title);
  marker.onclick=async event=>{
   event.stopPropagation();
   if(regionModeByScene[scene.scene_id])return;
   if(!tree.owner_scene_id){
    highlightedNeighbor=tree.tree_id;applyTreeSelection(scene,[]);
    zoomFocusByScene[scene.scene_id]={x:tree.x,y:tree.y,label:'Neighbor inventory reference'};
    update(scene.scene_id);card.querySelector('.mask-hint').textContent=marker.title+'. This marker is not an added tree.';return;
   }
   if(!await syncReviews())return;
   const url=new URL(location.href);url.pathname='/registration';
   url.searchParams.set('scene',tree.owner_scene_id);url.searchParams.set('tree_id',tree.tree_id);
   url.searchParams.set('fullscreen','1');url.searchParams.set('return',location.pathname+location.search);
   url.searchParams.delete('queue');url.searchParams.delete('queue_chip');
   if(!scene.splits.includes(tree.split)){url.searchParams.delete('run');url.searchParams.delete('compare_run');}
   location.href=url.pathname+url.search;
  };
  wrap.append(marker);
  const radius=tree.radius_m||((window.estimateCrown?.(tree)?.width_m||0)/2);
  if(radius>0){
   const anchor=samplesById[scene.sample_ids[0]||scene.anchor?.sample_id];
   const circle=document.createElement('span');circle.className='inventory-context inventory-context-crown';
   const px=radius/regionPixelScaleM(anchor);
   Object.assign(circle.style,{left:(100*tree.x/scene.image_width)+'%',top:(100*tree.y/scene.image_height)+'%',width:(200*px/scene.image_width)+'%',height:(200*px/scene.image_height)+'%'});
   wrap.append(circle);
  }
 }
}
function checkNewTree(scene,x,y){
 const outside=x<0||y<0||x>=scene.image_width||y>=scene.image_height;
 if(metadata.editor_scene_id&&outside&&!borderInventory.available){
  alert('Neighbor inventory is unavailable. Add this tree from its own tile after inventory has loaded.');return false;
 }
 const anchor=samplesById[scene.sample_ids[0]||scene.anchor?.sample_id];
 const distance=(px,py)=>Math.hypot(anchor.transform_a*(px-x)+anchor.transform_b*(py-y),anchor.transform_d*(px-x)+anchor.transform_e*(py-y));
 const candidates=[];
 for(const id of scene.sample_ids){
  if(['not-tree','duplicate'].includes(statusOf(id)))continue;
  const p=displayedPosition(samplesById[id]);candidates.push({distance:distance(p.x,p.y),id,label:'inventory tree '+samplesById[id].tree_id});
 }
 for(const region of maskRegions){
  if(region.mode!=='confirmed-tree'||region.tree_id)continue;
  const p=regionForScene(region,scene);if(p)candidates.push({distance:distance(p.image_x,p.image_y),regionId:region.region_id,label:'an existing added tree'});
 }
 for(const tree of borderInventory.trees){
  if(!['not-tree','duplicate'].includes(tree.status))candidates.push({distance:distance(tree.x,tree.y),neighbor:tree.tree_id,label:'neighbor inventory tree '+tree.tree_id});
 }
 const nearest=candidates.filter(c=>c.distance<=2).sort((a,b)=>a.distance-b.distance)[0];
 if(!nearest)return true;
 if(confirm(`There is ${nearest.label} ${nearest.distance.toFixed(1)} m from this center. Add a separate tree? Cancel to inspect the existing marker.`))return true;
 if(nearest.id)selectSample(scene.scene_id,nearest.id,false);
 else if(nearest.regionId)selectEditableTree(scene.scene_id,addedTreeKey(nearest.regionId));
 else {
  highlightedNeighbor=nearest.neighbor;
  document.querySelector('.card.fullscreen .inventory-context-marker[data-tree-id="'+CSS.escape(nearest.neighbor)+'"]')?.focus();
 }
 return false;
}
'''
