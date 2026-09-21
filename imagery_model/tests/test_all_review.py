import subprocess
from types import SimpleNamespace

from urban_tree_ml.all_review import ALL_REVIEW_JS, combined_summary


def test_combined_scores_never_cross_city_model_or_test(tmp_path):
    config = SimpleNamespace(inventory=SimpleNamespace(city='USBOS'))
    bundles = {key: SimpleNamespace(training_run_id='v16', config=config,
        chips=[{'chip_id': chip, 'missed': misses}], summary=lambda: {'display': {'resolution': .6}})
        for key, chip, misses in [('val', 'a', 3), ('train', 'b', 20)]}
    records = [dict(run_id=key, training_run_id=model, city=city, metrics={'split': split})
        for key, model, city, split in [('val','v16','USBOS','validation'),
            ('train','v16','USBOS','train'),('test','v16','USBOS','test'),
            ('sf','v16','USSFO','train'),('old','v15','USBOS','train')]]
    catalog = SimpleNamespace(bundle=lambda key: bundles[key], summary=lambda: {'runs': records})
    context = SimpleNamespace(config=SimpleNamespace(paths=SimpleNamespace(root=tmp_path)),raster=tmp_path/'boston.vrt')
    result = combined_summary(catalog, 'val', context)
    assert [(c['chip_id'],c['split'],c['inference_run']) for c in result['chips']] == [('a','validation','val'),('b','train','train')]


def test_mixed_sort_is_total_misses_and_nulls_last_and_routes_preserve_split():
    code = ALL_REVIEW_JS.split('async function initAllReview')[0]
    script = '''
const state={city:'USBOS',chips:[{chip_id:'a',split:'validation',inference_run:'v16::validation-usbos',missed:20},{chip_id:'b',split:'train',inference_run:'v16::train-usbos',missed:100},{chip_id:'c',split:'train',inference_run:null,missed:null}]};
const location={origin:'http://localhost',pathname:'/model',search:'?split=all&sort=missed'};
''' + code + '''
if(state.chips.sort((a,b)=>allReviewSort(a,b,'missed')).map(c=>c.chip_id).join()!=='b,a,c')throw Error('wrong global ranking');
for(const chip of state.chips){const url=new URL(allChipRoute(chip.chip_id,'queue'),location.origin);
if(url.pathname!==(chip.split==='train'?'/curate-training':'/curate'))throw Error('wrong split');
if(url.searchParams.get('run')!==(chip.inference_run||''))throw Error('wrong run');
if(!url.searchParams.get('return').includes('split=all'))throw Error('lost return filters');}
'''
    subprocess.run(['node','-e',script],check=True,capture_output=True,text=True)


def test_all_queue_storage_bounded_compact_and_failure_safe():
    code=ALL_REVIEW_JS.split('function saveAllReviewQueue',1)[1].split('function allReviewSort',1)[0]
    script='function saveAllReviewQueue'+code+'''
const data=new Map(),storage={get length(){return data.size},key:i=>[...data.keys()][i],getItem:k=>data.get(k),removeItem:k=>data.delete(k),setItem(k,v){if(v.length+[...data].filter(([x])=>x!==k).reduce((n,[,s])=>n+s.length,0)>40000)throw Error('quota');data.set(k,v)}};
data.set('annotation-draft','DO NOT TOUCH');data.set('validation-curation:validation',JSON.stringify({split:'validation'}));
for(let i=0;i<20;i++)data.set('validation-curation:legacy'+i,JSON.stringify({split:'all',padding:'x'.repeat(10000)}));
const rows=Array.from({length:500},(_,i)=>({chip_id:'chip'+i,split:i%2?'train':'validation',inference_run:'long-shared-model-name-'+(i%2)}));
if(!saveAllReviewQueue(storage,'current',rows,'usbos','/model?split=all'))throw Error('legacy recovery');
for(let i=0;i<50;i++)if(!saveAllReviewQueue(storage,'current',rows,'usbos','/model'))throw Error('repeat');
if(data.size>5)throw Error('render accumulation');
const q=JSON.parse(data.get('validation-curation:current'));
if(q.version!==3||q.runs||q.routes.length!==500||q.entries||q.routes[0][1]!=='usbos')throw Error('not compact chip-only queue');
if(data.get('annotation-draft')!=='DO NOT TOUCH'||!data.has('validation-curation:validation'))throw Error('unrelated data removed');
// Force the retry path: unrelated storage fills all remaining capacity.
data.set('other-app','x'.repeat(50000));
if(saveAllReviewQueue(storage,'blocked',rows,'usbos','/model'))throw Error('quota should be handled');
if(!data.has('other-app'))throw Error('unrelated storage evicted');
if(saveAllReviewQueue({get length(){throw Error('blocked')}},'x',rows,'usbos','/model'))throw Error('blocked storage');
'''
    subprocess.run(['node','-e',script],check=True,capture_output=True,text=True)
