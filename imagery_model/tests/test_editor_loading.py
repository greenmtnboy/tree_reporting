"""Execute the rendered editor load guard without touching real annotations."""
import json
import re
import subprocess

from urban_tree_ml.quality import _render_grouped_registration_html
from urban_tree_ml.studio_shell import render_studio_shell


def node(script):
    subprocess.run(['node', '-'], input=script, encoding='utf-8', check=True, capture_output=True)


def test_editor_scripts_parse_and_recovery_is_outside_inert_cards():
    html = _render_grouped_registration_html([], {'editor_scene_id': 'scene-558'}, [])
    assert html.index('id="review-load-error"') < html.index('<main id="cards">')
    for script in re.findall(r'<script>(.*?)</script>', html, re.S):
        subprocess.run(['node', '--check'], input=script, encoding='utf-8', check=True, capture_output=True)


def test_hydration_failure_preserves_draft_and_retry_unlocks():
    html = _render_grouped_registration_html([], {}, [])
    hydrate = html.split('async function hydrateServerReviews() {', 1)[1].split('window.studioViewState?.restore();', 1)[0]
    node('''
const assert=require('node:assert/strict');
const elements=new Map();const document={getElementById(id){if(!elements.has(id))elements.set(id,{});return elements.get(id)}};
const metadata={editor_scene_id:'scene-558'},cards={inert:true};
let serverRevision=null, reviewLoadError=null, reviews={local:'keep'},sceneReviews={},maskRegions=[],savedSnapshot;
let savedVersion=-1,editVersion=0,syncTimer,stores=0,schedules=0;
const storedState={draft_version:1,base_revision:'old',reviews:{tree:{status:'offset'}},delete_mask_regions:['mask']};
const original=JSON.stringify(storedState);
const withAlignedDefaults=x=>x,update=()=>{},storeLocalState=()=>stores++,syncReviews=()=>{};
const setTimeout=()=>schedules++;
let persisted={state_revision:'new',reviews:{server:'keep'},scene_reviews:{},mask_regions:[]};
let fetch=async()=>({ok:true,json:async()=>persisted});
async function hydrateServerReviews() {''' + hydrate + '''
(async()=>{
await hydrateServerReviews();
assert.equal(cards.inert,true);assert.equal(serverRevision,null);
assert.equal(JSON.stringify(storedState),original);assert.deepEqual(reviews,{local:'keep'});
assert.equal(stores,0);assert.equal(schedules,0);
assert.equal(document.getElementById('review-load-error').hidden,false);
assert.match(reviewLoadError,/pending browser draft/);
persisted.state_revision='old';
await hydrateServerReviews();
assert.equal(cards.inert,false);assert.equal(reviewLoadError,null);
assert.equal(document.getElementById('review-load-error').hidden,true);
assert.equal(reviews.tree.status,'offset');assert.equal(schedules,1);
fetch=async()=>{throw new Error('offline')};
await hydrateServerReviews();
assert.equal(cards.inert,true);assert.match(reviewLoadError,/offline/);
assert.equal(document.getElementById('retry-review-load').disabled,false);
})().catch(e=>{console.error(e);process.exitCode=1});
''')


def test_editor_navigation_scope_does_not_change_data_owner():
    html = render_studio_shell('<head></head><body></body>', 'ussfo', {'ussfo':'SF','usbos':'Boston'}, {'ussfo':'v3','usbos':'v3'})
    script = html.split(' const params=new URLSearchParams(location.search);', 1)[1].split(" if(['/','/runs'", 1)[0]
    for return_url, expected in [('/model?city=all', 'all'), ('/model?city=usbos', 'usbos'), ('https://elsewhere.test/model?city=all', 'ussfo'), ('/model', 'ussfo')]:
        node('const assert=require("node:assert/strict"); const runs={ussfo:"v3",usbos:"v3"};const page="/registration";'
             + 'const location={origin:"http://localhost"};const params=new URLSearchParams({city:"ussfo",scene:"scene-558",return:' + json.dumps(return_url) + '});'
             + script + '\nassert.equal(currentCity,' + json.dumps(expected) + ');assert.equal(params.get("city"),"ussfo");')
