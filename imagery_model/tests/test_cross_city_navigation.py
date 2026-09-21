import subprocess
from pathlib import Path
from types import SimpleNamespace

from urban_tree_ml.all_review import combined_summary


def test_cross_city_summary_keeps_same_chip_ids_distinct(tmp_path):
    records = []
    bundles = {}
    contexts = {}
    for city in ('ussfo', 'usbos'):
        config = SimpleNamespace(inventory=SimpleNamespace(city=city.upper()))
        contexts[city] = SimpleNamespace(config=SimpleNamespace(paths=SimpleNamespace(root=tmp_path)), raster=tmp_path/'image.vrt')
        for split in ('train', 'validation', 'test'):
            key = city + split
            records.append(dict(run_id=key, training_run_id='v19', city=city.upper(), metrics={'split': split}))
            bundles[key] = SimpleNamespace(training_run_id='v19', config=config,
                chips=[{'chip_id': 'same' if split == 'train' else split, 'missed': 10}],
                summary=lambda city=city: {'city': city.upper(), 'display': {'resolution': .6}})
    catalog = SimpleNamespace(bundle=lambda key: bundles[key], summary=lambda: {'runs': records})
    result = combined_summary(catalog, 'ussfotrain', contexts['ussfo'], contexts)
    assert len(result['chips']) == 4
    assert {c['key'] for c in result['chips'] if c['split'] == 'train'} == {'ussfo:same', 'usbos:same'}


def test_next_uses_current_url_not_queue_model_and_handles_city_collision():
    source = Path('src/urban_tree_ml/quality.py').read_text(encoding='utf-8')
    def extract(start, end):
        return source.split(start, 1)[1].split(end, 1)[0].replace('{{', '{').replace('}}', '}')
    queue = 'function validationQueueContext() {' + extract('function validationQueueContext() {{', '    function updateValidationQueueNav')
    move = 'async function moveScene(card, direction) {' + extract('async function moveScene(card, direction) {{', '    function update(')
    script = '''
const pageParameters=new URLSearchParams({city:'ussfo',queue:'q',queue_chip:'r000001_c000001',run:'new-model',compare_run:'comparison-model',threshold:'.4'});
let stored={split:'all',version:3,city:'all',chips:['r000001_c000001','r000001_c000001'],routes:[[0,'ussfo'],[0,'usbos']]};
const sessionStorage={getItem:()=>JSON.stringify(stored)};
const location={origin:'http://localhost',href:''};let queueNavigating=false,curationReturn='/model?city=all&split=training-all';
let save=true;const syncReviews=async()=>save;
''' + queue + move + '''
(async()=>{
await moveScene({},1);let u=new URL(location.href,location.origin);
if(u.searchParams.get('run')!=='new-model'||u.searchParams.get('compare_run')!=='comparison-model'||u.searchParams.get('threshold')!=='.4'||u.searchParams.get('city')!=='usbos')throw Error('live parameters lost');
pageParameters.set('city','usbos');if(validationQueueContext().index!==1)throw Error('city collision');
pageParameters.set('city','ussfo');queueNavigating=false;location.href='';save=false;await moveScene({},1);if(location.href)throw Error('navigated without save');
stored={split:'all',version:2,city:'ussfo',chips:['r000001_c000001','r000002_c000002'],routes:[[0,0],[0,0]],runs:['stale-model']};
queueNavigating=false;save=true;await moveScene({},1);u=new URL(location.href,location.origin);
if(u.searchParams.get('run')!=='new-model')throw Error('legacy queue restored stale model');
stored={split:'registration',version:4,city:'all',chips:['scene-1','scene-1'],cities:['ussfo','usbos']};
pageParameters.set('queue_chip','scene-1');queueNavigating=false;await moveScene({},1);u=new URL(location.href,location.origin);
if(u.searchParams.get('city')!=='usbos'||u.searchParams.get('run')!=='new-model'||u.pathname!=='/registration')throw Error('cross-city scene routing');
pageParameters.set('city','usbos');if(validationQueueContext().index!==1)throw Error('scene city collision');
})().catch(e=>{console.error(e);process.exit(1)});
'''
    subprocess.run(['node', '-e', script], check=True, capture_output=True, text=True)
