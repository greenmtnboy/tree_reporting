import subprocess

from urban_tree_ml.quality import _render_grouped_registration_html


def test_crown_visibility_tracks_hidden_stacks_and_exclusions():
    html = _render_grouped_registration_html([], {}, [])
    start = html.index("card.querySelectorAll('.crown-circle[data-sample-id]')")
    end = html.index('card.querySelectorAll(".tree-choice")', start)
    code = html[start:end]
    script = '''
const sample={sample_id:'tree',transform_a:.6,transform_b:0,transform_d:0,transform_e:-.6};
const samplesById={tree:sample},scene={image_width:512,image_height:512},maskRegions=[],reviews={};
const circle={dataset:{sampleId:'tree'},style:{},classList:{toggle(){}}},sceneSelection=new Set();
const card={querySelectorAll:()=>[circle]},showStacks={checked:false};
let stacked=true,excluded=false,status='aligned';
const isStacked=()=>stacked,isHiddenTree=()=>excluded,statusOf=()=>status;
const displayedPosition=()=>({x:25,y:40});
const window={estimateCrown:()=>({width_m:6})};
function render(){
''' + code + '''
}
render();if(!circle.hidden)throw Error('hidden stack leaked crown');
showStacks.checked=true;render();if(circle.hidden)throw Error('shown stack lost crown');
showStacks.checked=false;stacked=false;render();if(circle.hidden)throw Error('ordinary tree lost crown');
excluded=true;render();if(!circle.hidden)throw Error('excluded tree leaked crown');
excluded=false;status='not-tree';render();if(!circle.hidden)throw Error('not-tree leaked crown');
status='duplicate';render();if(!circle.hidden)throw Error('duplicate leaked crown');
'''
    subprocess.run(['node','-e',script],check=True,capture_output=True,encoding='utf-8')


def test_second_pass_transitions_and_filters():
    html = _render_grouped_registration_html([], {}, [])
    start = html.index('function setSceneDone(')
    end = html.index('function regionPixelScaleM', start)
    js = 'const sceneReviews={};function persist(){};' + html[start:end] + '''
setSceneDone('a',true);
if(!matchesSceneReviewFilter('not-final',undefined)||!matchesSceneReviewFilter('not-final',sceneReviews.a))throw Error('not final should include pending and first pass');
if(!matchesSceneReviewFilter('second-pending',sceneReviews.a))throw Error('first pass');
const first=sceneReviews.a.completed_at;
setSceneMoreDone('a',true);
if(matchesSceneReviewFilter('not-final',sceneReviews.a))throw Error('final review must be excluded');
if(sceneReviews.a.completed_at!==first||!sceneReviews.a.done)throw Error('lost first pass');
if(!matchesSceneReviewFilter('more-done',sceneReviews.a)||matchesSceneReviewFilter('second-pending',sceneReviews.a))throw Error('second filter');
setSceneMoreDone('a',false);
if(!sceneReviews.a.done||sceneReviews.a.more_done)throw Error('undo second');
setSceneMoreDone('b',true);
if(!sceneReviews.b.done)throw Error('second implies first');
setSceneDone('b',false);
if(sceneReviews.b||!matchesSceneReviewFilter('pending',sceneReviews.b))throw Error('clear both');
'''
    subprocess.run(['node','-e',js],check=True,capture_output=True,encoding='utf-8')


def test_second_pass_advance_waits_for_save():
    html = _render_grouped_registration_html([], {}, [])
    start = html.index('let completingScene=null;')
    end = html.index('function regionPixelScaleM', start)
    js = '''
const a={dataset:{scene:'a'},classList:{contains:k=>k==='fullscreen'}};
const b={dataset:{scene:'b'},classList:{contains:()=>false}};
const document={querySelectorAll:()=>[a,b]},sceneStatusFilter={value:'not-final'},statusFilter={value:''};
const pageParameters={get:()=>null};let expanded=null,saveResolve,pinned=false;
const scenesById={a:{scene_id:'a'},b:{scene_id:'b'}};
function matchingScenes(){return Object.values(scenesById)}
function sceneMatches(){return true}function openScene(id){expanded=id==='a'?a:b}
function setSceneMoreDone(){pinned=completingScene==='a'}
function syncReviews(){return new Promise(r=>saveResolve=r)}
function update(){}function setExpanded(card){expanded=card}
''' + html[start:end] + '''
(async()=>{
 const pending=completeSecondPass(a);
 if(!pinned||expanded)throw Error('must keep current fullscreen until saved');
 saveResolve(true);await pending;
 if(expanded!==b||completingScene!==null)throw Error('must advance to captured next');
 expanded=null;
 const failed=completeSecondPass(a);saveResolve(false);await failed;
 if(expanded)throw Error('must not navigate on save failure');
})().catch(e=>{console.error(e);process.exitCode=1});
'''
    subprocess.run(['node','-e',js],check=True,capture_output=True,encoding='utf-8')


def test_nudges_batch_storage_and_render_only_active_scene():
    html = _render_grouped_registration_html([], {}, [])
    start = html.index('function persist(sceneId=null)')
    end = html.index('function selectionFor', start)
    js = """
let localSaveTimer=null,nudgeFrame=null,syncTimer=null,localWrites=0,editVersion=0;
const rendered=[],frames=new Map(),timers=new Map();let serial=0;
const document={getElementById:()=>({textContent:''})};
const storeLocalState=()=>localWrites++,update=id=>rendered.push(id),syncReviews=()=>{};
const setTimeout=(fn,ms)=>{const id=++serial;timers.set(id,{fn,ms});return id};
const clearTimeout=id=>timers.delete(id);
const requestAnimationFrame=fn=>{const id=++serial;frames.set(id,fn);return id};
const cancelAnimationFrame=id=>frames.delete(id);
""" + html[start:end] + """
persist('scene-1');persist('scene-1');persist('scene-1');
if(localWrites||rendered.length||frames.size!==1)throw Error('nudge work was not batched');
for(const fn of frames.values())fn();
if(rendered.length!==1||rendered[0]!=='scene-1')throw Error('wrong render scope');
const local=[...timers.values()].filter(t=>t.ms===200);
if(local.length!==1)throw Error('multiple storage writes');local[0].fn();
if(localWrites!==1)throw Error('local state not stored');
"""
    subprocess.run(['node','-e',js],check=True,capture_output=True,encoding='utf-8')


def test_hidden_trees_removed_from_interaction_and_navigation_flushes():
    html = _render_grouped_registration_html([], {}, [])
    assert "new Set(['not-tree','uncertain','duplicate','occluded'])" in html
    assert 'const selectableSamples = stackVisibleSamples.filter(sample => !isHiddenTree(sample))' in html
    assert "|| isHiddenTree(sample)) return false" in html
    assert "hideInput.className='hide-excluded-chip'" in html
    assert 'clearTimeout(localSaveTimer);storeLocalState();' in html
    assert 'if (!await syncReviews())' in html
    assert 'if(!onlyScene)samples.forEach' in html


def test_save_deduplicates_and_waits_for_edits_during_request():
    html = _render_grouped_registration_html([], {}, [])
    start=html.index('let reviewSaveChain = Promise.resolve();')
    end=html.index('function storeLocalState()',start)
    js="""
let syncTimer=null,localSaveTimer=null,serverRevision='r1',calls=0,injectEdit=false,fail=false;
const reviewLoadError=null;
const metadata={},reviews={},sceneReviews={},maskRegions=[];
const document={getElementById:()=>({textContent:''})},location={protocol:'http:'};
const storeLocalState=()=>{};
const fetch=async()=>{calls++;if(injectEdit){injectEdit=false;editVersion++;}
return {ok:!fail,json:async()=>({state_revision:'r'+calls,reviews:1,mask_regions:0,completed_scenes:0,error:'failed'})}};
"""+html[start:end]+"""
(async()=>{
if(!await syncReviews()||calls!==1)throw Error('initial save');
await Promise.all([syncReviews(),syncReviews()]);if(calls!==1)throw Error('duplicate save');
editVersion++;injectEdit=true;
if(!await syncReviews()||calls!==3||savedVersion!==editVersion)throw Error('lost in-flight edit');
editVersion++;fail=true;if(await syncReviews())throw Error('failure allowed navigation');
fail=false;if(!await syncReviews()||savedVersion!==editVersion)throw Error('retry failed');
})().catch(e=>{console.error(e);process.exitCode=1});
"""
    subprocess.run(['node','-e',js],check=True,capture_output=True,encoding='utf-8')


def test_queue_entry_builds_one_card_without_pruning_annotation_state():
    html=_render_grouped_registration_html([],{},[])
    start=html.index('const focusedQueueEntry=')
    end=html.index('document.addEventListener("keydown"',start)
    code=html[start:end]
    for queued,expected in [(True,1),(False,12)]:
        js="const scenes=Array.from({length:200},(_,i)=>({scene_id:'s'+i}));"+"""
const scenesById=Object.fromEntries(scenes.map(s=>[s.scene_id,s]));
const requestedSceneId='s75',curationReturn='/model',built=[];
let mounted=[];
const document={createElement:()=>({style:{},setAttribute(){},append(){},addEventListener(){}}),querySelector:()=>null};
const createCard=s=>{built.push(s);return {dataset:{scene:s.scene_id},remove(){mounted=mounted.filter(c=>c!==this)}}};
const cards={before(){},append:c=>mounted.push(c),querySelectorAll:()=>mounted,
 get children(){return mounted},insertBefore(c,b){mounted=mounted.filter(x=>x!==c);const i=mounted.indexOf(b);mounted.splice(i<0?mounted.length:i,0,c)},
 querySelector:selector=>mounted.find(c=>selector.includes('"'+c.dataset.scene+'"'))};
const CSS={escape:s=>s},closeStreetView=()=>{};
"""+f"const pageParameters=new URLSearchParams('{ 'queue=fixture' if queued else '' }');"+code+f"""
sceneMatches=()=>true;
mountScenePage();
if(mounted.length!=={expected}||scenes.length!==200)throw Error('wrong mount scope');
scenePage=1;mountScenePage();
if(mounted.length!=={expected})throw Error('unbounded mount');
if(!focusedQueueEntry&&mounted[0].dataset.scene!=='s12')throw Error('wrong second page');
sceneMatches=s=>s.scene_id==='s199';mountScenePage();
if(!focusedQueueEntry&&(mounted.length!==1||mounted[0].dataset.scene!=='s199'))throw Error('filter/clamp');
"""
        subprocess.run(['node','-e',js],check=True,capture_output=True,encoding='utf-8')
