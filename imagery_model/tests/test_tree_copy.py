from pathlib import Path
import subprocess


def test_copy_whitelists_fields_and_number_is_stable():
    source = Path('src/urban_tree_ml/quality.py').read_text(encoding='utf-8')
    start = source.index('    function addedTreeNumber(')
    end = source.index('\n    }', source.index('    function treeCopyTemplate(', start)) + 7
    code = source[start:end].replace('{{', '{').replace('}}', '}')
    subprocess.run(['node', '-e', 'const window={estimateCrown:()=>({width_m:8})};\n' + code + '''
const tree={species:'Quercus alba',tree_id:'inventory-1',dbh:12,status:'not-tree'};
const copy=treeCopyTemplate(tree,{crown_radius_m:5});
if(JSON.stringify(copy)!==JSON.stringify({radius_m:5,species:'Quercus alba',identification_source:''}))throw Error('Unsafe copy');
if(treeCopyTemplate(tree).radius_m!==4)throw Error('Estimated crown fallback');
if(addedTreeNumber('uuid')!==addedTreeNumber('uuid')||!/^A[0-9]{3}$/.test(addedTreeNumber('uuid')))throw Error('Unstable marker');
'''], check=True, capture_output=True, text=True)


def test_paste_uses_new_region_identity_and_cursor():
    source = Path('src/urban_tree_ml/quality.py').read_text(encoding='utf-8')
    assert 'finishMaskRegion(sceneId,point.x,point.y,copiedTree.radius_m/regionPixelScaleM(anchor),null,copiedTree)' in source
    assert 'if(event.repeat)return' in source
    assert "globalThis.crypto.randomUUID()" in source


def test_pasted_tree_is_persisted_with_fresh_world_coordinates():
    source = Path('src/urban_tree_ml/quality.py').read_text(encoding='utf-8')
    code = source[source.index('    function finishMaskRegion('):source.index('    function armCrownSizing(')]
    code = code.replace('{{', '{').replace('}}', '}')
    subprocess.run(['node', '-e', '''
const scenesById={s:{sample_ids:['t']}};
const activeByScene={s:'t'}, samplesById={t:{target_x:10,target_y:20,world_x:100,world_y:200,transform_a:.6,transform_b:0,transform_d:0,transform_e:-.6}};
const regionModeByScene={s:'confirmed-tree'},regionDraftByScene={},metadata={curation_schema_version:2},selectedAddedByScene={};
const deselectedScenes=new Set();
let maskRegions=[],saved=0;
const regionPixelScaleM=()=>.6,persist=()=>saved++,update=()=>{};
''' + code + '''
finishMaskRegion('s',30,40,5,null,{species:'Quercus alba',identification_source:'',tree_id:'DO NOT COPY',dbh:99});
const first=maskRegions[0];
if(first.world_x!==112||first.world_y!==188||first.radius_m!==3||first.species!=='Quercus alba')throw Error('Geometry or species lost');
if('tree_id' in first||'dbh' in first||'scene_id' in first)throw Error('Copied source identity');
regionModeByScene.s='confirmed-tree';finishMaskRegion('s',40,50,5,null,{});
if(maskRegions[1].region_id===first.region_id||saved!==2)throw Error('New identities/save');
'''], check=True, capture_output=True, text=True)


def test_rendered_scripts_parse():
    import re
    from urban_tree_ml.quality import _render_grouped_registration_html
    html = _render_grouped_registration_html([], {}, [])
    for script in re.findall(r'<script[^>]*>(.*?)</script>', html, re.S):
        subprocess.run(['node', '-e', 'new Function(require("fs").readFileSync(0,"utf8"))'],
                       input=script, check=True, capture_output=True, text=True, encoding='utf-8')
