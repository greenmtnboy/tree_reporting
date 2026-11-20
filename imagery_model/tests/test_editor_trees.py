import subprocess

from urban_tree_ml.editor_trees import EDITOR_TREES_JS


def test_shared_tree_selection_movement_and_undo():
    script = r'''
const assert=require('node:assert/strict');
const scene={scene_id:'s',sample_ids:['a','hidden','reference','stack'],image_width:100,image_height:100};
const base={target_x:20,target_y:30,transform_a:.6,transform_b:0,transform_d:0,transform_e:-.6};
const samplesById={a:{...base,sample_id:'a'},hidden:{...base,hidden:true},reference:{...base,reference_only:true},stack:{...base,stack:true}};
const scenesById={s:scene},showStacks={checked:false},reviews={},selectedByScene={},selectedAddedByScene={},activeByScene={s:'a'},zoomFocusByScene={},deselectedScenes=new Set(),moveUndoByScene={};
let maskRegions=[{region_id:'manual',mode:'confirmed-tree',image_x:40,image_y:30,radius_m:2},
 {region_id:'peer',mode:'confirmed-tree',world_x:60,world_y:30,longitude:1,latitude:2,radius_m:2},
 {region_id:'owned',mode:'confirmed-tree',tree_id:'a',image_x:20,image_y:30},
 {region_id:'background',mode:'confirmed-background',image_x:20,image_y:30},
 {region_id:'outside',mode:'confirmed-tree',image_x:90,image_y:90},
 {region_id:'grouped',mode:'confirmed-tree',image_x:50,image_y:50}];
const isHiddenTree=s=>s.hidden,isStacked=s=>s.stack;
const pointInVegetationGroup=p=>p.x===50&&p.y===50;
const displayedPosition=s=>({x:reviews[s.sample_id]?.image_x??s.target_x,y:reviews[s.sample_id]?.image_y??s.target_y});
const regionForScene=r=>({...r,image_x:r.world_x??r.image_x,image_y:r.world_y??r.image_y,anchor_sample_id:'a'});
const selectionFor=id=>selectedByScene[id]||new Set(['a']);
function update(){}function persist(){}function editableBorder(){return 0;}
function pushReviewUndo(id,x){(moveUndoByScene[id]||=[]).push(x);}
''' + EDITOR_TREES_JS + r'''
assert.deepEqual([...editableTrees(scene).keys()],['a','added:manual','added:peer','added:outside']);
selectTreesInBox(scene,{x:65,y:35},{x:15,y:25}); // reverse drag, mixed sources
assert.deepEqual(selectedEditableTrees('s').map(t=>t.key),['a','added:manual','added:peer']);
const before=JSON.stringify(maskRegions);
nudgeEditableTrees(scene,[1,0],5);
assert.equal(reviews.a.image_x,25);assert.equal(maskRegions[0].image_x,45);
assert.equal(maskRegions[1].world_x,63);assert.equal(maskRegions[1].longitude,undefined);
assert.equal(maskRegions[2].image_x,20); // linked crown has no duplicate mutation
assert.equal(maskRegions[3].image_x,20); // background never selected
undoTreeEdit('s',moveUndoByScene.s.pop());
assert.deepEqual(reviews,{});assert.equal(JSON.stringify(maskRegions),before);
// Multiple manual trees and no inventory points are still one selection.
selectTreesInBox(scene,{x:35,y:25},{x:65,y:35});
assert.equal(selectedEditableTrees('s').length,2);assert.equal(selectedByScene.s.size,0);
classifyEditableTrees('s','not-tree');
assert.ok(!maskRegions.some(r=>['manual','peer'].includes(r.region_id)));
undoTreeEdit('s',moveUndoByScene.s.pop());
assert.equal(JSON.stringify(maskRegions),before);assert.equal(selectedEditableTrees('s').length,2);
// Pure inventory gestures use the same abstraction, and replacement clears manuals.
selectEditableTree('s','a');assert.equal(selectedEditableTrees('s').length,1);
assert.equal(selectedAddedByScene.s,undefined);
classifyEditableTrees('s','occluded');assert.equal(reviews.a.status,'occluded');
undoTreeEdit('s',moveUndoByScene.s.pop());assert.deepEqual(reviews,{});
// Empty rectangle explicitly clears selection rather than retaining a stale tree.
selectTreesInBox(scene,{x:0,y:0},{x:2,y:2});assert.equal(selectedEditableTrees('s').length,0);
// Empty inventory tile, peer tree and edge clamping.
scene.sample_ids=[];
selectTreesInBox(scene,{x:0,y:0},{x:99,y:99});assert.equal(selectedEditableTrees('s').length,3);
nudgeEditableTrees(scene,[1,0],20);
assert.equal(maskRegions.find(r=>r.region_id==='outside').image_x,99);
assert.equal(maskRegions.find(r=>r.region_id==='manual').image_x,49);
undoTreeEdit('s',moveUndoByScene.s.pop());assert.equal(JSON.stringify(maskRegions),before);
'''
    subprocess.run(['node', '-e', script], check=True, capture_output=True, text=True)
