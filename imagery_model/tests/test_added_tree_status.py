from pathlib import Path
import subprocess
from urban_tree_ml.editor_trees import EDITOR_TREES_JS


def test_added_tree_status_never_mutates_previous_inventory_selection():
    source=Path('src/urban_tree_ml/quality.py').read_text(encoding='utf-8')
    code=source[source.index('    function setStatus('):source.index('    function runVegetationHeuristic(')]
    code=code.replace('{{','{').replace('}}','}')
    subprocess.run(['node','-e','''
let selectedAddedByScene={},maskRegions=[],reviews={},saved=0;
const selectionFor=()=>new Set(['inventory']);
const persist=()=>saved++;
const scenesById={s:{scene_id:'s',sample_ids:['inventory']}},samplesById={inventory:{sample_id:'inventory'}},
 showStacks={checked:true},isHiddenTree=()=>false,isStacked=()=>false,displayedPosition=()=>({x:0,y:0}),
 pointInVegetationGroup=()=>false,regionForScene=r=>({...r,anchor_sample_id:'inventory'}),
 deselectedScenes=new Set(),selectedByScene={},activeByScene={},zoomFocusByScene={},moveUndoByScene={};
const pushReviewUndo=()=>{},update=()=>{};
''' + EDITOR_TREES_JS + code + '''
for(const status of ['uncertain','not-tree','duplicate']){
 delete treeSelectionByScene.s;deselectedScenes.clear();selectedAddedByScene={s:'added'};maskRegions=[{region_id:'added',mode:'confirmed-tree'}];
 reviews={inventory:{status:'aligned',east_m:42}};const before=JSON.stringify(reviews),n=saved;
 setStatus('s',status);setStatus('s',status);
 if(maskRegions.length||saved!==n+1||JSON.stringify(reviews)!==before)throw Error('Wrong target or repeated deletion');
}
delete treeSelectionByScene.s;deselectedScenes.clear();selectedAddedByScene={s:'added'};maskRegions=[{region_id:'added',mode:'confirmed-tree'}];
const before=JSON.stringify(reviews);setStatus('s','aligned');setStatus('s','occluded');
if(JSON.stringify(reviews)!==before||maskRegions.length!==1)throw Error('Unsupported status fell through');
selectEditableTree('s','inventory');setStatus('s','uncertain');
if(reviews.inventory.status!=='uncertain')throw Error('Inventory selection cannot resume');
'''],check=True,capture_output=True,text=True)
