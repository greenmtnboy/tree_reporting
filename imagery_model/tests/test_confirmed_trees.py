import json
import re
import subprocess
from pathlib import Path

import pandas as pd
import pytest

from urban_tree_ml.config import load_config
from urban_tree_ml.confirmed_trees import confirmed_tree_rows
from urban_tree_ml.feedback import persist_review_payload, load_persisted_reviews
from urban_tree_ml.splits import assign_spatial_splits
from urban_tree_ml.quality import _render_grouped_registration_html


def test_confirmed_centers_respect_spatial_splits_and_have_no_attributes():
    config = load_config(Path(__file__).parents[1] / 'configs/sf_naip_baseline.yaml')
    coordinates = pd.DataFrame({'longitude': [-122.5 + i * .001 for i in range(150)],
                                'latitude': [37.76] * 150})
    expected = assign_spatial_splits(coordinates, config.split, config.seed)
    regions = [dict(region_id=str(i), mode='confirmed-tree',
                    anchor_longitude=row.longitude, anchor_latitude=row.latitude,
                    east_m=0., north_m=0., radius_m=3., splits=['train', 'validation'])
               for i, row in enumerate(coordinates.itertuples(index=False))]
    actual = confirmed_tree_rows(regions, config, 'EPSG:32610')
    eligible = expected[expected.split_eligible & (expected.split != 'test')]
    assert set(actual.tree_id) == {f'manual-{i}' for i in eligible.index}
    assert not actual.empty
    assert not actual.species_eligible.any()
    assert not actual.genus_eligible.any()
    assert not actual.dbh_eligible.any()
    assert actual.dbh_log1p.isna().all()
    assert (actual.crown_radius_m == 3).all()


def test_added_crown_roundtrips_geographic_storage(tmp_path):
    manifest = {'metadata': {'curation_schema_version': 2, 'curation_crs': 'EPSG:32610',
                             'review_id': 'image', 'source_raster': 'image.tif'},
                'samples': [], 'scenes': []}
    (tmp_path / 'manifest.json').write_text(json.dumps(manifest))
    crown = dict(region_id='crown-1', mode='confirmed-tree', longitude=-122.45,
                 latitude=37.76, radius_m=4.2, source='human')
    persist_review_payload(tmp_path, {'mask_regions': [crown]})
    payload = json.loads((tmp_path / 'reviews.json').read_text())
    assert payload['mask_regions'] == []
    saved = payload['added_trees']['manual-crown-1']
    assert saved['longitude'] == crown['longitude']
    assert saved['crown_radius_m'] == 4.2
    assert 'scene_id' not in saved
    assert load_persisted_reviews(tmp_path)['mask_regions'][0]['mode'] == 'confirmed-tree'


def test_tree_tool_requires_draw_and_is_distinct_from_protection():
    html = _render_grouped_registration_html([], {}, [])
    assert 'Add tree crown' in html
    assert 'Add tree crown (T)' in html
    assert "event.key.toLowerCase()==='t'" in html
    assert "if(!event.repeat)setRegionMode(openCard.dataset.scene,'confirmed-tree')" in html
    assert 'mode === "confirmed-tree" && radiusPx < 1' in html
    assert 'radius does not widen scoring tolerance' in html
    scripts = re.findall(r'<script[^>]*>(.*?)</script>', html, re.S)
    subprocess.run(['node', '-e', 'new Function(require("fs").readFileSync(0,"utf8"))'],
                   input='\n'.join(scripts), encoding='utf-8', capture_output=True, check=True)


def test_crown_drag_moves_geographic_center_once_and_cancel_does_not_save():
    html = _render_grouped_registration_html([], {}, [])
    code = html[html.index('function bindCrownDrag('):html.index('function renderMaskRegions(')]
    harness = r'''
let saves=0, renders=0;
const persist=()=>saves++, renderMaskRegions=()=>renders++;
const rememberMove=()=>{};
const stored={region_id:'r',world_x:100,world_y:200,longitude:1,latitude:2,radius_m:4};
const maskRegions=[stored];
const handlers={}; let capture=false;
const marker={style:{},classList:{add(){},remove(){}},
 addEventListener(name,fn){handlers[name]=fn},setPointerCapture(){capture=true},
 hasPointerCapture(){return capture},releasePointerCapture(){capture=false}};
const card={dataset:{},querySelector(){return {getBoundingClientRect(){return {left:0,top:0,width:200,height:100}}}}};
const scene={image_width:100,image_height:100};
const region={...stored,image_x:20,image_y:30};
const anchor={transform_a:.6,transform_b:.1,transform_d:.2,transform_e:-.6};
bindCrownDrag(marker,card,scene,region,anchor);
const event=(x,y)=>({button:0,pointerId:1,clientX:x,clientY:y,preventDefault(){},stopPropagation(){}});
handlers.pointerdown(event(40,30)); handlers.pointermove(event(60,35));
if(saves || stored.world_x!==100)throw Error('saved during drag');
handlers.pointerup(event(60,35));
if(saves!==1 || stored.world_x!==106.5 || stored.world_y!==199 || stored.radius_m!==4)throw Error('bad affine move');
if('longitude' in stored || 'latitude' in stored)throw Error('stale geographic coordinates');
if(card.dataset.ignoreImageClick!=='true')throw Error('drag click would delete crown');
handlers.pointerdown(event(40,30)); handlers.pointermove(event(80,60)); handlers.pointercancel(event(80,60));
if(saves!==1 || renders!==1 || stored.world_x!==106.5)throw Error('cancel committed');
handlers.pointerdown(event(40,30)); handlers.pointerup(event(40,30));
if(saves!==1)throw Error('click saved');
'''
    subprocess.run(['node', '-e', code + harness], encoding='utf-8', capture_output=True, check=True)


def test_linked_crown_follows_offset_and_saves_absolute_center():
    html = _render_grouped_registration_html([], {}, [])
    code = html[html.index('function syncLinkedCrownCenters('):html.index('function bindCrownDrag(')]
    js = r'''
const sample={sample_id:'s',tree_id:'t',scene_id:'scene',target_x:10,target_y:20,
 world_x:100,world_y:200,transform_a:.6,transform_b:.1,transform_d:.2,transform_e:-.6};
const samples=[sample],samplesById={s:sample};
let point={x:20,y:25};const displayedPosition=()=>point,regionPixelScaleM=()=>.6;
const linked={mode:'confirmed-tree',tree_id:'t',world_x:100,world_y:200,longitude:1,latitude:2,radius_m:6};
const added={mode:'confirmed-tree',world_x:150,world_y:250,radius_m:3};
const maskRegions=[linked,added],scene={scene_id:'scene',sample_ids:['s'],image_width:100,image_height:100};
''' + code + r'''
let display=regionForScene(linked,scene);
if(display.image_x!==20||display.image_y!==25)throw Error('circle detached');
syncLinkedCrownCenters();
if(linked.world_x!==106.5||linked.world_y!==199||linked.radius_m!==6)throw Error('bad saved center');
if('longitude' in linked||'latitude' in linked)throw Error('stale lat/lon');
if(added.world_x!==150||added.world_y!==250)throw Error('unlinked crown moved');
point={x:10,y:20};syncLinkedCrownCenters();
if(linked.world_x!==100||linked.world_y!==200)throw Error('reset did not follow');
'''
    subprocess.run(['node', '-e', js], capture_output=True, encoding='utf-8', check=True)


def test_selected_crown_sizing_arms_at_corrected_center_without_saving():
    html = _render_grouped_registration_html([], {}, [])
    code = html[html.index('function armCrownSizing('):html.index('function regionForScene(')]
    js = '''
const selectionFor=()=>new Set(['s']),samplesById={s:{tree_id:'t'}},activeByScene={scene:'s'},reviews={};
const displayedPosition=()=>({x:42,y:73}),maskRegions=[{mode:'confirmed-tree',tree_id:'t',radius_m:6}];
const regionModeByScene={},regionDraftByScene={},regionPixelScaleM=()=>.6;
let updated=0;const update=()=>updated++,window={};
const setRegionMode=(id)=>{regionModeByScene[id]=null;delete regionDraftByScene[id]};
''' + code + '''
armCrownSizing('scene');const d=regionDraftByScene.scene;
if(d.x!==42||d.y!==73||d.radius_px!==10||d.tree_id!=='t')throw Error('incorrect centered draft');
if(maskRegions[0].radius_m!==6)throw Error('arming mutated saved crown');
armCrownSizing('scene');if(regionDraftByScene.scene)throw Error('toggle did not cancel');
'''
    subprocess.run(['node','-e',js],capture_output=True,encoding='utf-8',check=True)
    assert "prompt('Measured crown radius" not in html


def test_radius_hotkey_saves_cursor_distance_without_dragging():
    html = _render_grouped_registration_html([], {}, [])
    code = html[html.index('function armCrownSizing('):html.index('function syncLinkedCrownCenters(')]
    js = r'''
let selected=1;const selectionFor=()=>({size:selected});
const samplesById={s:{sample_id:'s',tree_id:'t'}},activeByScene={scene:'s'};
const displayedPosition=()=>({x:20,y:30}),regionPixelScaleM=()=>.5;
const regionModeByScene={},regionDraftByScene={},maskRegions=[],reviews={},window={};
let saved=[];const finishMaskRegion=(...args)=>saved.push(args),update=()=>{},setRegionMode=()=>{};
''' + code + r'''
armCrownSizing('scene',{x:23,y:34});
if(JSON.stringify(saved[0])!==JSON.stringify(['scene',20,30,5,'t']))throw Error('wrong radius or center');
armCrownSizing('scene',{x:10000,y:30});if(saved[1][3]!==1000)throw Error('radius not bounded');
selected=2;armCrownSizing('scene',{x:23,y:34});if(saved.length!==2)throw Error('multiselect sized');
'''
    subprocess.run(['node','-e',js],capture_output=True,text=True,check=True)


def test_inventory_crown_does_not_bind_independent_drag():
    html = _render_grouped_registration_html([], {}, [])
    code = html[html.index('function bindCrownDrag('):html.index('function renderMaskRegions(')]
    js = code + "bindCrownDrag({addEventListener(){throw Error('drag bound')}},null,null,{tree_id:'t'},null);"
    subprocess.run(['node','-e',js],capture_output=True,text=True,check=True)
