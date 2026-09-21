import subprocess

from urban_tree_ml.quality import _render_grouped_registration_html


def test_confirm_requires_successful_save_and_selection_stays_empty():
    html=_render_grouped_registration_html([],{},[])
    functions=html.split('    function selectionFor(sceneId)',1)[1].split('    function updateSelectionFocus',1)[0]
    script='''const assert=require('node:assert/strict');
const deselectedScenes=new Set(),confirmingScenes=new Set(),selectedAddedByScene={};
const selectedByScene={s:new Set(['a'])},activeByScene={s:'a'},samplesById={a:{}},isHiddenTree=()=>false;
const zoomFocusByScene={},regionModeByScene={},regionDraftByScene={};let editVersion=1,ok=false;
const clearNudgeFocus=()=>{},update=()=>{},syncReviews=async()=>ok;
function selectionFor(sceneId)''' + functions + '''
(async()=>{
await confirmSelection('s');assert.equal(selectionFor('s').has('a'),true);
ok=true;await confirmSelection('s');assert.equal(selectionFor('s').size,0);
assert.equal(selectionFor('s').size,0);assert.equal(activeByScene.s,'a');
deselectedScenes.delete('s');selectedAddedByScene.s='manual';
await confirmSelection('s');assert.equal(selectedAddedByScene.s,undefined);assert.equal(selectionFor('s').size,0);
})().catch(e=>{console.error(e);process.exitCode=1});
'''
    result=subprocess.run(['node','-'],input=script,encoding='utf-8',capture_output=True)
    assert result.returncode==0,result.stderr


def test_local_focus_preserves_far_labels_and_selected_markers():
    html=_render_grouped_registration_html([],{},[])
    helper=html.split('    function updateSelectionFocus(card,scene)',1)[1].split('    function setStatus',1)[0]
    script='''const assert=require('node:assert/strict');
const regionPixelScaleM=()=>1,samplesById={a:{}},selectionFor=()=>new Set(['a']);
const displayedPosition=()=>({x:20,y:20}),reviews={},window={},maskRegions=[],selectedAddedByScene={},deselectedScenes=new Set();
function el(x,classes){const s=new Set(classes);return {style:{left:x+'%',top:'20%'},classList:{contains:k=>s.has(k),toggle:(k,v)=>v?s.add(k):s.delete(k)},s};}
const near=el(25,['tree-marker']),far=el(60,['tree-species-label']),active=el(20,['tree-marker','active']);
const card={querySelectorAll:()=>[near,far,active]},scene={scene_id:'s',sample_ids:['a'],image_width:100,image_height:100};
function updateSelectionFocus(card,scene)''' + helper + '''
updateSelectionFocus(card,scene);assert.equal(near.s.has('local-dim'),true);
assert.equal(far.s.has('local-focus'),false);assert.equal(active.s.has('local-dim'),false);
'''
    result=subprocess.run(['node','-'],input=script,encoding='utf-8',capture_output=True)
    assert result.returncode==0,result.stderr
