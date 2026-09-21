import subprocess

from urban_tree_ml.compare_overlay import COMPARE_OVERLAY
from urban_tree_ml.model_debug import CHIP_COMPARE_HTML
from urban_tree_ml.quality import _render_grouped_registration_html
from urban_tree_ml.curation_compare import CURATION_COMPARE_JS


def test_overlay_markers_use_run_colors_shared_threshold_and_geometry():
    start = COMPARE_OVERLAY.index('function overlayCompatible')
    end = COMPARE_OVERLAY.index('function renderOverlay')
    script = "const esc=v=>String(v).replaceAll('<','&lt;');\n" + COMPARE_OVERLAY[start:end] + """
const a={run:{run_id:'v5'},display:{output_stride:2,chip_pixels:256,resolution_m:.6},data:{predictions:[{output_x:64,output_y:32,score:.4,species:'oak'},{output_x:0,output_y:0,score:.2}]}};
let html=overlayMarkers(a,'a',.35);
if(!html.includes('left:50%;top:25%')||!html.includes('overlay-a')||html.includes('left:0%'))throw Error(html);
if((overlayMarkers(a,'b',.1).match(/<i /g)||[]).length!==2)throw Error('threshold');
if(!overlayCompatible(a,a)||overlayCompatible(a,{display:{...a.display,resolution_m:1}}))throw Error('geometry');
"""
    subprocess.run(['node','-e',script],check=True,capture_output=True,text=True)


def test_compare_page_has_overlay_shared_threshold_and_independent_toggles():
    assert 'const threshold=overlayThreshold(),' in CHIP_COMPARE_HTML
    for name in ['overlay-a','overlay-b','show-overlay-a','show-overlay-b','overlay-fullscreen']:
        assert f'id="{name}"' in CHIP_COMPARE_HTML
    assert 'colors identify runs, not correctness' in CHIP_COMPARE_HTML


def test_fullscreen_loads_only_one_prediction_path():
    html = _render_grouped_registration_html([], {}, [])
    code = html[html.index('function setExpanded('):html.index('function validationQueueContext(')]
    js = r'''
let curationComparisonEnabled=true,single=0,compare=0;
const button={setAttribute(){}},card={dataset:{scene:'s'},classList:{add(){},remove(){}},querySelector:()=>button};
const document={querySelectorAll:()=>[],querySelector:()=>card,body:{classList:{toggle(){}}}};
const scenesById={s:{}},curationReturn=null,streetViewEmbedApiKey=null;
const updateValidationQueueNav=()=>{},loadPredictionOverlay=()=>single++,toggleCurationCompare=()=>compare++;
''' + code + r'''
setExpanded(card,true);if(single!==0||compare!==1)throw Error('duplicate prediction loads');
curationComparisonEnabled=false;setExpanded(card,true);
if(single!==1||compare!==1)throw Error('wrong selected-only path');
'''
    subprocess.run(['node','-e',js],check=True,capture_output=True,text=True)


def test_both_comparison_selectors_load_only_requested_pair_and_reuse_cache():
    js = r'''
const nodes=[],requests=[];
function node(){const n={style:{},children:[],handlers:{},attrs:{},value:'',dataset:{},
 append(...v){this.children.push(...v)},setAttribute(k,v){this.attrs[k]=v},
 addEventListener(k,v){this.handlers[k]=v},querySelectorAll(){return []},
 classList:{add(){},remove(){},toggle(){}}};nodes.push(n);return n;}
const localStorage={getItem(){return null},setItem(){}},requestedPredictionThreshold=.35;
let predictionRun='a',curationReturn='/model?run=a&assignment=dense&split=train';
const location={origin:'http://localhost',href:'http://localhost/registration?run=a&queue=q&scene=scene-1'};
const pageParameters=new URL(location.href).searchParams;
const history={replaceState(a,b,url){location.href=new URL(url,location.origin).href}};
let queue={run:'a',chips:['chip-1','chip-2']};
const sessionStorage={setItem(key,value){queue=JSON.parse(value)}};
const validationQueueContext=()=>{if(queue.run!==pageParameters.get('run'))throw Error('queue invalidated');return {id:'q',queue}};
const document={createElement:node,createTextNode:t=>t,querySelectorAll:()=>[]};
const wrap=node(),badge=node(),button=node();button.parentElement=node();
const card=node();card.querySelector=s=>s==='.prediction-badge'?badge:wrap;
const scene={validation_chip_id:'chip-1'};
const entry=id=>({available:true,run:{run_id:id},display:{chip_pixels:256,output_stride:2,resolution_m:.6},data:{predictions:[{score:.5,output_x:3,output_y:4}]}});
const fetch=async url=>{requests.push(url);const q=new URL(url,'http://localhost').searchParams;
return {ok:true,json:async()=>({selected_run_id:q.get('run'),choices:['a','b','c'].map(run_id=>({run_id})),
runs:[entry(q.get('run')),entry(q.get('compare_run')==='auto'?'b':q.get('compare_run'))]})};};
const loadPredictionOverlay=()=>{throw Error('single overlay loaded')};
''' + CURATION_COMPARE_JS + r'''
(async()=>{
await toggleCurationCompare(card,scene,button);
const current=nodes.find(n=>n.attrs['aria-label']==='Current model');
const other=nodes.find(n=>n.attrs['aria-label']==='Comparison model');
if(!current||!other||current.value!=='a'||other.value!=='b')throw Error('missing selectors');
current.value='c';await current.handlers.change();
let q=new URL(requests.at(-1),'http://localhost').searchParams;
if(q.get('run')!=='c'||q.get('compare_run')!=='b')throw Error('wrong pair');
const count=requests.length;other.value='a';await other.handlers.change();
if(requests.length!==count)throw Error('cached model fetched again');
if(!badge.title.includes('(c)'))throw Error('current model did not change');
const saved=new URL(location.href).searchParams;
if(saved.get('run')!=='c'||saved.get('compare_run')!=='a'||predictionRun!=='c')throw Error('selection not persisted');
if(queue.run!=='a')throw Error('model change should not rewrite the navigation queue');
if(!curationReturn.includes('assignment=dense')||!curationReturn.includes('run=c'))throw Error('return filters lost');
if(pageParameters.get('queue')!=='q'||pageParameters.get('scene')!=='scene-1')throw Error('navigation state lost');
const nextCard=node();nextCard.querySelector=s=>s==='.prediction-badge'?badge:wrap;
await toggleCurationCompare(nextCard,{validation_chip_id:'chip-2'},button);
q=new URL(requests.at(-1),'http://localhost').searchParams;
if(q.get('run')!=='c'||q.get('compare_run')!=='a')throw Error('next chip forgot pair');
})().catch(e=>{console.error(e);process.exitCode=1});
'''
    subprocess.run(['node','-e',js],check=True,capture_output=True,text=True)
