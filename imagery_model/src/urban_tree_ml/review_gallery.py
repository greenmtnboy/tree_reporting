"""Lightweight gallery and scoped, merge-only editor transport.

Caller holds review_state_lock for reads and writes. Cache keys include both file
versions; cached objects must not be mutated. Existing full-state APIs stay valid.
"""
import json
import math
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

from urban_tree_ml.feedback import (
    ReviewStateConflictError, load_persisted_reviews, normalize_mask_region_payload,
    normalize_review_payload, normalize_scene_review_payload, persist_review_payload,
    _write_json_atomic, _review_state_sha256,
)
from urban_tree_ml.tree_curation import tree_records, expand_tree_records, transformers, storage_payload


_primed = {}


def collection_version(directory):
    def version(name):
        path = Path(directory) / name
        if not path.exists(): return None
        stat = path.stat()
        return stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size
    return version('manifest.json'), version('reviews.json')


def prime_collection(directory, version, manifest, state, stored=None):
    key = str(Path(directory))
    _primed.pop(key, None)
    _primed[key] = (version, manifest, state, stored)
    while len(_primed) > 4:
        _primed.pop(next(iter(_primed)))


@lru_cache(maxsize=4)
def _read_version(directory, manifest_version, state_version):
    manifest = json.loads((Path(directory) / 'manifest.json').read_text(encoding='utf-8'))
    state = load_persisted_reviews(directory)
    return manifest, state


def read_collection(directory):
    directory = Path(directory)
    version = collection_version(directory)
    cached = _primed.get(str(directory))
    if cached and cached[0] == version:
        return cached[1], cached[2]
    manifest, state = _read_version(str(directory), *version)
    prime_collection(directory, version, manifest, state)
    return manifest, state


def scoped_manifest(manifest, scene_id):
    scene = next((s for s in manifest['scenes'] if s['scene_id'] == scene_id), None)
    if scene is None:
        raise ValueError('Unknown review scene')
    ids = set(scene['sample_ids'])
    return {'metadata': {**manifest['metadata'], 'editor_scene_id': scene_id},
            'scenes': [scene], 'samples': [s for s in manifest['samples'] if s['sample_id'] in ids]}


def intersects(region, scoped):
    if not scoped['samples']:
        return False
    sample, scene = scoped['samples'][0], scoped['scenes'][0]
    transformer = transformers(scoped['metadata']['curation_crs'])[0]
    x, y = transformer.transform(sample['longitude'], sample['latitude'])
    if 'longitude' in region:
        rx, ry = transformer.transform(region['longitude'], region['latitude'])
    else:
        rx, ry = region['world_x'], region['world_y']
    a, b, d, e = (sample[k] for k in ['transform_a', 'transform_b', 'transform_d', 'transform_e'])
    determinant = a * e - b * d
    px = sample['target_x'] + ((rx-x)*e-(ry-y)*b)/determinant
    py = sample['target_y'] + ((ry-y)*a-(rx-x)*d)/determinant
    # Conservative bounding rectangle includes circles crossing a chip edge.
    radius_x = region['radius_m'] * math.hypot(e, b) / abs(determinant)
    radius_y = region['radius_m'] * math.hypot(d, a) / abs(determinant)
    return -radius_x <= px <= scene['image_width']+radius_x and -radius_y <= py <= scene['image_height']+radius_y


def scene_state(manifest, state, scene_id):
    scoped = scoped_manifest(manifest, scene_id)
    ids = set(scoped['scenes'][0]['sample_ids'])
    return {**{k: state[k] for k in ['schema_version', 'state_revision']},
            'reviews': {k:v for k,v in state['reviews'].items() if k in ids},
            'scene_reviews': {k:v for k,v in state['scene_reviews'].items() if k == scene_id},
            'mask_regions': [r for r in state['mask_regions'] if intersects(r, scoped)]}


def apply_scene_patch(directory, scene_id, patch):
    manifest, state = read_collection(directory)
    version = _primed[str(Path(directory))][0]
    if patch.get('base_revision') != state['state_revision']:
        raise ReviewStateConflictError('Annotations changed in another tab; export unsaved edits and reload before saving')
    scoped = scoped_manifest(manifest, scene_id)
    if not set(patch.get('reviews', {})) <= set(scoped['scenes'][0]['sample_ids']):
        raise ValueError('Tree patch is outside the loaded chip')
    if not set(patch.get('scene_reviews', {})) <= {scene_id} or not set(patch.get('delete_scene_reviews', [])) <= {scene_id}:
        raise ValueError('Completion patch is outside the loaded chip')
    changed = normalize_review_payload({'reviews': patch.get('reviews', {})}, manifest)
    # Copy only containers being changed; nested unmodified records are immutable.
    merged = {**state, 'reviews': dict(state['reviews']), 'scene_reviews': dict(state['scene_reviews'])}
    # Update every projected view of a changed tree using its canonical world position.
    merged['reviews'].update(expand_tree_records(tree_records(changed, manifest), manifest))
    merged['scene_reviews'].update(normalize_scene_review_payload({'scene_reviews': patch.get('scene_reviews', {})}, manifest))
    for key in patch.get('delete_scene_reviews', []):
        merged['scene_reviews'].pop(key, None)
    visible_masks = {r['region_id'] for r in state['mask_regions'] if intersects(r, scoped)}
    deleted = set(patch.get('delete_mask_regions', []))
    if not deleted <= visible_masks:
        raise ValueError('Mask deletion is outside the loaded chip')
    regions = normalize_mask_region_payload({'mask_regions': patch.get('mask_regions', [])}, manifest)
    old_ids = {r['region_id'] for r in state['mask_regions']}
    for region in regions:
        if region['region_id'] in old_ids and region['region_id'] not in visible_masks:
            raise ValueError('Mask update is outside the loaded chip')
        if not intersects(region, scoped):
            raise ValueError('Mask does not intersect the loaded chip')
    upserts = {r['region_id']:r for r in regions}
    merged['mask_regions'] = [r for r in state['mask_regions'] if r['region_id'] not in deleted | set(upserts)] + list(upserts.values())
    merged.update(metadata=manifest['metadata'], base_revision=state['state_revision'])
    if manifest['metadata'].get('curation_schema_version') == 2:
        cached = _primed[str(Path(directory))][3]
        if cached is None:
            cached = storage_payload(state, manifest)
        # Serialize tree-keyed data directly. Only changed trees are projected;
        # already validated city records never pass through the full normalizer again.
        region_storage = storage_payload({'reviews': {}, 'mask_regions': merged['mask_regions']}, manifest)
        stored = {**cached, **region_storage,
                  'tree_reviews': {**cached['tree_reviews'], **tree_records(changed, manifest)},
                  'scene_reviews': merged['scene_reviews'], 'metadata': manifest['metadata'],
                  'saved_at': datetime.now(UTC).isoformat()}
        for transient in ['state_revision', 'base_revision']:
            stored.pop(transient, None)
        merged['mask_regions'] = sorted(merged['mask_regions'], key=lambda r: r['region_id'])
        revision = _review_state_sha256(merged['reviews'], merged['scene_reviews'], merged['mask_regions'])
        if collection_version(directory) != version:
            raise ReviewStateConflictError('Annotations changed during save; reload before saving')
        path = Path(directory) / 'reviews.json'
        _write_json_atomic(path, stored)
        merged['state_revision'] = revision
        prime_collection(directory, collection_version(directory), manifest, merged, stored)
        return dict(path=str(path), reviews=len(merged['reviews']), completed_scenes=len(merged['scene_reviews']),
                    mask_regions=len(merged['mask_regions']), state_revision=revision)
    return persist_review_payload(directory, merged)


def gallery_page(manifest, state, query, *, paginate=True):
    value = lambda key, default='': query.get(key, [default])[0]
    status, split = value('status'), value('split')
    rows = []
    for scene in manifest['scenes']:
        if not scene['sample_ids'] or not set(scene.get('splits', [])) & {'train', 'validation'}:
            continue
        review = state['scene_reviews'].get(scene['scene_id'], {})
        done, final = bool(review.get('done')), bool(review.get('more_done'))
        if split and split not in scene['splits']:
            continue
        if status and not {'pending':not done, 'done':done, 'second-pending':done and not final,
                           'not-final':not final, 'more-done':final}.get(status, False):
            continue
        rows.append({k:scene.get(k) for k in ['scene_id','image','validation_chip_id','splits','tree_count']} |
                    {'done':done,'more_done':final})
    page = max(0, min(int(value('page', '0')), max(0, (len(rows)-1)//12)))
    return {'total':len(rows), 'page':page, 'page_size':12, 'items':rows[page*12:(page+1)*12] if paginate else rows,
            'queue':[r['scene_id'] for r in rows]}


def combined_gallery(collections, query):
    rows = []
    for city, (manifest, state) in collections.items():
        rows.extend({**row, 'city': city} for row in gallery_page(manifest, state, query, paginate=False)['items'])
    rows.sort(key=lambda r: (r['scene_id'], r['city']))
    page = max(0, min(int(query.get('page', ['0'])[0]), max(0, (len(rows)-1)//12)))
    return {'total': len(rows), 'page': page, 'page_size': 12,
            'items': rows[page*12:(page+1)*12],
            'queue': [r['scene_id'] for r in rows], 'queue_cities': [r['city'] for r in rows]}


GALLERY_HTML = '''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Registration gallery</title><style>
body{margin:0;background:#101b15;color:#e4eee7;font:16px system-ui}main{max-width:1400px;margin:auto;padding:24px}
.toolbar{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin:16px 0}
button,select,.curate{font:inherit;color:inherit;background:#263b2e;border:1px solid #526b59;border-radius:7px;padding:9px 14px;cursor:pointer;text-decoration:none}
button:disabled{opacity:.4;cursor:default}#gallery{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:16px}
article{padding:12px;border:1px solid #405747;border-radius:10px}article img{width:100%;aspect-ratio:1;object-fit:contain;background:#0a110d}article p{color:#b5cbbb}.curate{display:inline-block}
</style></head><body><main><h1>Registration gallery</h1><p>Images load one page at a time. Tree annotations and area masks load only when you open curation.</p>
<div class="toolbar"><label>Split <select id="split"><option value="">Train + validation</option><option value="train">Train</option><option value="validation">Validation</option></select></label>
<label>Review status <select id="status"><option value="">All images</option><option value="pending">To review</option><option value="done">Done (any pass)</option><option value="second-pending">Done — needs second pass</option><option value="not-final">Not final reviewed</option><option value="more-done">More done (second pass)</option></select></label>
<button id="publish">Publish city feedback</button><span id="message" role="status"></span></div>
<nav class="toolbar" aria-label="Review image pages"><button id="prev">Previous page</button><span id="count"></span><button id="next">Next page</button></nav><div id="gallery"></div>
<script>
const parameters=new URLSearchParams(location.search),city=parameters.get('city')||'ussfo';
const split=document.getElementById('split'),status=document.getElementById('status'),message=document.getElementById('message');
const filterKey='registration-gallery:'+city;
const saved=JSON.parse(localStorage.getItem(filterKey)||'{}');
split.value=parameters.get('split')??saved.split??'';status.value=parameters.get('status')??saved.status??'';
let page=Number(parameters.get('page')??saved.page??0),generation=0;
const queueId=crypto.randomUUID();
document.getElementById('publish').textContent=city==='all'?'Publish all cities':'Publish city feedback';
async function load(){
 const ticket=++generation;message.textContent='Loading image summaries…';
 const q=new URLSearchParams({city,split:split.value,status:status.value,page:String(page)});
 if(parameters.get('run'))q.set('run',parameters.get('run'));
 try{const response=await fetch('/api/review-gallery?'+q);const data=await response.json();if(!response.ok)throw Error(data.error);if(ticket!==generation)return;
 page=data.page;q.set('page',String(page));history.replaceState(null,'','/registration?'+q);
 try{localStorage.setItem(filterKey,JSON.stringify({split:split.value,status:status.value,page}))}catch{}
 let navigationQueue=queueId;
 try{
  const old=[];for(let i=0;i<sessionStorage.length;i++){const key=sessionStorage.key(i);if(key.startsWith('validation-curation:')&&key!=='validation-curation:'+queueId){try{if(JSON.parse(sessionStorage.getItem(key))?.split==='registration')old.push(key)}catch{}}}
  while(old.length>2)sessionStorage.removeItem(old.shift());
  sessionStorage.setItem('validation-curation:'+queueId,JSON.stringify({split:'registration',version:4,city,chips:data.queue,cities:data.queue_cities||data.queue.map(()=>city)}))
 }catch{navigationQueue=''}
 const gallery=document.getElementById('gallery');gallery.replaceChildren();
 for(const item of data.items){const card=document.createElement('article'),title=document.createElement('h2'),img=document.createElement('img'),details=document.createElement('p'),link=document.createElement('a');
 const owner=item.city||city;
 title.textContent=owner.toUpperCase()+' · '+(item.validation_chip_id||item.scene_id);img.src='/city-assets/'+encodeURIComponent(owner)+'/'+item.image;img.loading='lazy';img.alt='Imagery preview '+title.textContent;
 details.textContent=item.tree_count+' inventory trees · '+item.splits.join(' / ')+' · '+(item.more_done?'More done':item.done?'Done':'To review');
 const target=new URLSearchParams({city:owner,scene:item.scene_id,fullscreen:'1',queue:navigationQueue,queue_chip:item.scene_id,return:'/registration?'+q});
 if(!navigationQueue){target.delete('queue');target.delete('queue_chip')}
 if(parameters.get('run'))target.set('run',parameters.get('run'));link.href='/registration?'+target;link.className='curate';link.textContent='Curate';
 card.append(title,img,details,link);gallery.append(card);}
 document.getElementById('count').textContent=data.total?`${page*12+1}–${Math.min((page+1)*12,data.total)} of ${data.total} images`:'No matching images';
 document.getElementById('prev').disabled=page===0;document.getElementById('next').disabled=(page+1)*12>=data.total;message.textContent='';
 }catch(error){if(ticket===generation)message.textContent='Load failed: '+error.message;}
}
for(const control of [split,status])control.addEventListener('change',()=>{page=0;load()});
document.getElementById('prev').onclick=()=>{page--;load()};document.getElementById('next').onclick=()=>{page++;load()};
document.getElementById('publish').onclick=async()=>{const button=document.getElementById('publish');button.disabled=true;message.textContent='Publishing saved feedback…';try{const r=await fetch('/api/finalize?city='+encodeURIComponent(city),{method:'POST'});const result=await r.json();if(result.cities){message.textContent=result.cities.map(c=>c.city.toUpperCase()+': '+(c.ok?'published':'FAILED — '+c.error)).join(' · ')}else{if(!r.ok)throw Error(result.error);message.textContent='City feedback published'}}catch(e){message.textContent=e.message}finally{button.disabled=false}};
load();
</script></main></body></html>'''
