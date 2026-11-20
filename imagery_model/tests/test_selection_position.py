import json
import subprocess

from urban_tree_ml.quality import _render_grouped_registration_html


def run(script):
    result=subprocess.run(['node','-'],input=script,encoding='utf-8',capture_output=True)
    assert result.returncode==0,result.stderr


def test_manual_nudge_moves_geographic_crown_and_records_undo():
    from urban_tree_ml.editor_trees import EDITOR_TREES_JS
    run("""const assert=require('node:assert/strict');
const added={region_id:'manual',mode:'confirmed-tree',world_x:100,world_y:200,longitude:1,latitude:2,radius_m:4};
const scene={scene_id:'s',sample_ids:[],image_width:256,image_height:256};
const scenesById={s:scene},samplesById={a:{transform_a:.6,transform_b:0,transform_d:0,transform_e:-.6}};
const maskRegions=[added],reviews={},selectedAddedByScene={s:'manual'},deselectedScenes=new Set();
const regionForScene=r=>({image_x:20+(r.world_x-100)/.6,image_y:30-(r.world_y-200)/.6,anchor_sample_id:'a'}),pointInVegetationGroup=()=>false;
const zoomFocusByScene={},moveUndoByScene={},editableBorder=()=>0;
let undo=0,saves=0;const pushReviewUndo=()=>undo++,persist=()=>saves++;
""" + EDITOR_TREES_JS + """
nudgeEditableTrees(scene,[0,-1],5);
assert.equal(added.world_y,203);assert.equal(added.world_x,100);
assert.equal(added.radius_m,4);assert.equal(added.longitude,undefined);
assert.equal(zoomFocusByScene.s.y,25);assert.equal(undo,1);assert.equal(saves,1);
""")


def test_street_view_key_tracks_current_position_and_manual_selection():
    html=_render_grouped_registration_html([],{},[])
    project=html.split('    function addedCrownStreetViewTarget',1)[1].split('    function renderStreetViewCamera',1)[0]
    refresh=html.split('    function refreshStreetView(card, sample)',1)[1].split('    async function refreshStreetViewNow',1)[0]
    run('''const assert=require('node:assert/strict');
function addedCrownStreetViewTarget''' + project + '''
function refreshStreetView(card,sample)''' + refresh + '''
const sample={sample_id:'a',target_x:10,target_y:10,latitude:42,longitude:-71,transform_a:.6,transform_b:0,transform_d:0,transform_e:-.6};
const samplesById={a:sample},scenesById={s:{scene_id:'s'}},selectedAddedByScene={},maskRegions=[];
let x=10,calls=0;const displayedPosition=()=>({x,y:10});
const setTimeout=()=>++calls,clearTimeout=()=>{},refreshStreetViewNow=()=>{};
const card={dataset:{scene:'s'}};
const selectedEditableTrees=()=>[],deselectedScenes=new Set(),reviews={};
refreshStreetView(card,sample);const first=card.dataset.streetViewRequest;
refreshStreetView(card,sample);assert.equal(calls,1);
x=15;refreshStreetView(card,sample);assert.notEqual(card.dataset.streetViewRequest,first);assert.equal(calls,2);
maskRegions.push({region_id:'manual',mode:'confirmed-tree',image_x:30,image_y:40,anchor_sample_id:'a'});
selectedAddedByScene.s='manual';const regionForScene=r=>r;
refreshStreetView(card,sample);assert.match(card.dataset.streetViewRequest,/manual:manual/);
''')
