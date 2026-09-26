from pathlib import Path
import subprocess


def test_move_undo_groups_repeats_preserves_other_edits_and_saves():
    source=Path('src/urban_tree_ml/quality.py').read_text(encoding='utf-8')
    code=source[source.index('    const moveUndoByScene'):source.index('    function selectionFor')]
    code=code.replace('{{','{').replace('}}','}')
    subprocess.run(['node','-e','''
const reviews={a:{status:'aligned'},b:{status:'offset',image_x:4,image_y:6}},zoomFocusByScene={};
let maskRegions=[],saves=0;const persist=()=>saves++;
''' + code + '''
rememberMove('s',['a','b']);
reviews.a={status:'offset',image_x:10,image_y:12,crown_radius_m:7,note:'keep'};
reviews.b.image_x=9;
rememberMove('s',['a','b'],null,true);reviews.a.image_x=20;
undoMove('s');
if(reviews.a.status!=='aligned'||'image_x' in reviews.a||reviews.b.image_x!==4||reviews.a.crown_radius_m!==7||reviews.a.note!=='keep')throw Error('Group/repeat or independent edits');
if(saves!==1||moveUndoByScene.s.length)throw Error('Save/history');
rememberMove('s',['a']);reviews.a.status='not-tree';reviews.a.image_x=8;undoMove('s');
if(reviews.a.status!=='not-tree')throw Error('Restored an excluded target');
maskRegions=[{region_id:'c',world_x:100,world_y:200,radius_m:8}];
rememberMove('s',[],maskRegions[0]);maskRegions[0].world_x=110;maskRegions[0].radius_m=9;
undoMove('other');if(maskRegions[0].world_x!==110)throw Error('Cross scene undo');
undoMove('s');if(maskRegions[0].world_x!==100||maskRegions[0].radius_m!==9)throw Error('Crown undo');
rememberMove('s',[],maskRegions[0]);maskRegions=[];undoMove('s');if(maskRegions.length)throw Error('Resurrected deleted crown');
for(let i=0;i<60;i++)rememberMove('s',['a']);if(moveUndoByScene.s.length!==50)throw Error('Unbounded history');
'''],check=True,capture_output=True,text=True)
