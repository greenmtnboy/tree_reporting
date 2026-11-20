import subprocess

from urban_tree_ml.single_tree_ui import SINGLE_TREE_JS


def test_empty_tile_polygons_finish_render_and_keep_drag_georeference():
    script = r'''
const assert=require('node:assert/strict');
const anchor={sample_id:'tile-anchor',target_x:256,target_y:256,
 world_x:1000,world_y:2000,transform_a:.3,transform_b:0,transform_d:0,transform_e:-.3};
const scene={scene_id:'empty',sample_ids:[],anchor,image_width:512,image_height:512};
const samplesById={'tile-anchor':anchor},scenesById={empty:scene};
const regionDraftByScene={},regionModeByScene={},selectedAddedByScene={};
const deselectedScenes=new Set();
let maskRegions=[],saved=0,dragAnchor;
function persist(id){assert.equal(id,'empty');saved++;}
function update(){}
function alert(message){throw Error(message);}
function statusOf(){return 'aligned';}
function displayedPosition(sample){return {x:sample.target_x,y:sample.target_y};}
function regionForScene(region){return {image_x:150,image_y:150};}
function bindCrownDrag(poly,card,scene,region,anchor){dragAnchor=anchor;}
const element=()=>({namespaceURI:'svg',dataset:{},style:{},classList:{add(){},toggle(){}},
 setAttribute(){},append(){},addEventListener(){}});
const document={createElementNS:()=>element()};
const wrap={append(){},closest(){return {};}};
'''+SINGLE_TREE_JS+r'''
const points=[{x:100,y:100},{x:200,y:100},{x:200,y:200},{x:100,y:200}];
for(const mode of ['confirmed-background','confirmed-woods','copse-polygon','shrub-mass-polygon','single-tree']){
 maskRegions=mode==='single-tree'?[{mode:'confirmed-tree',region_id:'human'}]:[];
 const draft={mode,points,pointer_id:7};
 regionDraftByScene.empty=draft;regionModeByScene.empty=mode;
 finishSingleArea('empty',draft);
 const region=maskRegions.at(-1);
 assert.equal(region.mode,mode.replace('-polygon',''));
 assert.equal(regionModeByScene.empty,null);
 assert.equal(regionDraftByScene.empty,undefined);
 assert.deepEqual(region.world_polygon[0],[953.2,2046.8]);
 const restored=singleAreaPixels(region,scene);
 restored.forEach((p,i)=>{assert.ok(Math.abs(p.x-points[i].x)<1e-9);assert.ok(Math.abs(p.y-points[i].y)<1e-9);});
 renderSingleArea(wrap,scene,region,false);
 assert.equal(dragAnchor,anchor);
}
assert.equal(saved,5);
// Inventory-backed scenes continue to use their original inventory anchor.
scene.sample_ids=['inventory'];samplesById.inventory={...anchor,sample_id:'inventory'};
renderSingleArea(wrap,scene,maskRegions.at(-1),false);
assert.equal(dragAnchor,samplesById.inventory);
'''
    subprocess.run(['node','-e',script],check=True,capture_output=True,text=True)


def test_full_tile_background_one_click_exact_bounds_and_duplicate():
    script = r'''
const assert=require('node:assert/strict');
const anchor={sample_id:'a',target_x:128,target_y:128,world_x:1000,world_y:2000,
 transform_a:.6,transform_b:0,transform_d:0,transform_e:-.6};
const scene={scene_id:'tile',sample_ids:[],anchor,image_width:256,image_height:256,context_pixels:16};
const scenesById={tile:scene},samplesById={a:anchor},metadata={curation_schema_version:2};
const regionModeByScene={tile:'confirmed-woods'},regionDraftByScene={tile:{points:[]}};
let maskRegions=[],saved=0,alerts=[];
function confirm(){throw Error('Full-tile background must not prompt');}
function alert(message){alerts.push(message);}
function persist(scene){assert.equal(scene,'tile');saved++;}
function update(){}
function statusOf(){return 'aligned';}
function displayedPosition(s){return {x:s.target_x,y:s.target_y};}
function regionForScene(){return null;}
'''+SINGLE_TREE_JS+r'''
confirmTileBackground('tile');
assert.equal(saved,1);assert.equal(maskRegions.length,1);
assert.deepEqual(maskRegions[0].world_polygon,[[923.2,2076.8],[1076.8,2076.8],[1076.8,1923.2],[923.2,1923.2]]);
assert.equal(maskRegions[0].mode,'confirmed-background');assert.equal(maskRegions[0].source,'human');
assert.equal(regionModeByScene.tile,null);assert.equal(regionDraftByScene.tile,undefined);
confirmTileBackground('tile');
assert.equal(saved,1);assert.equal(maskRegions.length,1);assert.equal(alerts.length,1);
// Existing tree reviews are not rewritten, and older unsupported schemas cannot write.
scene.sample_ids=['a'];maskRegions=[];metadata.curation_schema_version=1;
confirmTileBackground('tile');assert.equal(saved,1);assert.equal(maskRegions.length,0);
'''
    subprocess.run(['node','-e',script],check=True,capture_output=True,text=True)
