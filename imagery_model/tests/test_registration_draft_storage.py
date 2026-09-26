import subprocess
from pathlib import Path


def test_pending_drafts_and_quota_do_not_block_server_save():
    source = Path('src/urban_tree_ml/quality.py').read_text(encoding='utf-8')
    def function(start, end):
        return source.split(start, 1)[1].split(end, 1)[0].replace('{{', '{').replace('}}', '}')
    store = 'function storeLocalState() {' + function('function storeLocalState() {{', '    function persist(')
    sync = 'function syncReviews() {' + function('function syncReviews() {{', '    async function performReviewSave(')
    delta = 'function reviewDelta(snapshot, baseline) {' + function('function reviewDelta(snapshot, baseline) {{', '    let editVersion=')
    script = '''
const data=new Map([['unrelated-draft','keep']]);let fail=false;
const localStorage={setItem(k,v){if(fail)throw Error('quota');data.set(k,v)},removeItem:k=>data.delete(k)};
const document={getElementById:()=>({textContent:''})};
const reviewLoadError=null,metadata={};
const storageKey='current';let serverRevision='r1',editVersion=1,savedVersion=0;
let reviews={a:{status:'aligned'},b:{status:'uncertain'}},sceneReviews={},maskRegions=[];
let savedSnapshot={reviews:{a:{status:'aligned'}},scene_reviews:{},mask_regions:[]};
let syncTimer,localSaveTimer,reviewSaveChain=Promise.resolve(),calls=0;
async function performReviewSave(){calls++;savedVersion=editVersion;storeLocalState();return true}
''' + delta + store + sync + '''
(async()=>{
if(!storeLocalState())throw Error('draft not stored');
const draft=JSON.parse(data.get('current'));
if(draft.reviews.a||!draft.reviews.b||draft.base_revision!=='r1')throw Error('not a delta');
fail=true;
if(!await syncReviews()||calls!==1)throw Error('quota blocked server save');
if(data.has('current'))throw Error('acknowledged draft retained');
if(data.get('unrelated-draft')!=='keep')throw Error('other drafts removed');
editVersion++;performReviewSave=async()=>false;
if(await syncReviews())throw Error('failed server save allowed navigation');
if(savedVersion===editVersion)throw Error('failed save marked clean');
})().catch(e=>{console.error(e);process.exit(1)});
'''
    subprocess.run(['node', '-e', script], check=True, capture_output=True, text=True)
