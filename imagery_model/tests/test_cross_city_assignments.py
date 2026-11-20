import json
import subprocess
from types import SimpleNamespace

from urban_tree_ml.all_review import ALL_REVIEW_JS, combined_summary


def test_assignment_list_spans_cities_without_chip_id_collisions(tmp_path):
    records, bundles, contexts = [], {}, {}
    assignments = {
        'usbos': {'same': {'queue': 'Perimeter'}, 'boston': {'queue': 'Logan airport edges'}},
        'ussfo': {'same': {'queue': 'Parkland'}, 'sf': {'queue': 'Perimeter'}},
    }
    for city, chips in assignments.items():
        contexts[city] = SimpleNamespace(config=SimpleNamespace(paths=SimpleNamespace(root=tmp_path)), raster=tmp_path/'image.vrt')
        config = SimpleNamespace(inventory=SimpleNamespace(city=city.upper()))
        for split in ('train', 'validation', 'test'):
            key = city + split
            records.append(dict(run_id=key, training_run_id='v18', city=city.upper(), metrics={'split': split}))
            bundles[key] = SimpleNamespace(training_run_id='v18', config=config,
                chips=[{'chip_id': chip} for chip in (chips if split == 'train' else [split])],
                summary=lambda city=city: {'city': city.upper(), 'display': {},
                    'assignments': {'snapshot': 'snapshot-'+city,
                                    'chips': {**assignments[city], 'test': {'queue': 'Sealed'}}}})
    catalog = SimpleNamespace(bundle=lambda key: bundles[key], records=lambda: records,
                              summary=lambda: {'runs': records})
    result = combined_summary(catalog, 'usbostrain', contexts['usbos'], contexts)
    indexed = result['assignments']['chips']
    assert indexed['usbos:same']['queue'] == 'Perimeter'
    assert indexed['ussfo:same']['queue'] == 'Parkland'
    assert {k for k, v in indexed.items() if v['queue'] == 'Perimeter'} == {'usbos:same', 'ussfo:sf'}
    assert 'Sealed' not in {v['queue'] for v in indexed.values()}
    assert result['assignments']['snapshots'] == {'usbos': 'snapshot-usbos', 'ussfo': 'snapshot-ussfo'}
    single = combined_summary(catalog, 'usbostrain', contexts['usbos'])
    assert single['assignments']['chips'] == {k:v for k,v in indexed.items() if k.startswith('usbos:')}

    # Exercise the actual browser filtering and queue routing with overlapping IDs.
    script = 'const state='+json.dumps(result)+';\n'
    script += "const location={origin:'http://localhost',pathname:'/model',search:'?city=all&split=all'};\n"
    script += ALL_REVIEW_JS.split('async function initAllReview')[0]
    script += """
const assignments=state.assignments.chips;
const pick=tag=>state.chips.filter(c=>matchesAllReviewAssignment(c,assignments,tag));
if(pick('Perimeter').map(c=>c.key).sort().join()!=='usbos:same,ussfo:sf')throw Error('cross-city membership');
if(pick('Logan airport edges').map(c=>c.key).join()!=='usbos:boston')throw Error('Logan membership');
if(pick('assigned').length!==4||pick('all').length!==6)throw Error('all filters');
for(const chip of pick('Perimeter')){
 const url=new URL(allChipRoute(chip.key,'q'),location.origin);
 if(url.searchParams.get('city')!==chip.city||url.searchParams.get('chip')!==chip.chip_id||url.pathname!=='/curate-training')throw Error('wrong city route');
}
const storage={length:0,setItem:(key,value)=>{storage.value=JSON.parse(value)}};
saveAllReviewQueue(storage,'q',pick('Perimeter'),'all','/model?city=all');
if(storage.value.routes.map(r=>r[1]).join()!=='usbos,ussfo')throw Error('queue city lost');
location.href='http://localhost/model?city=all&run=v18&split=validation&threshold=0.13&chip=old&queue=old';
const destination=new URL(assignmentReviewUrl({run_id:'runways::train-usbos',queue:'Logan east-south runways'}),location.origin);
if(destination.searchParams.get('run')!=='runways::train-usbos'||destination.searchParams.get('assignment')!=='Logan east-south runways')throw Error('assignment owner lost');
if(destination.searchParams.get('city')!=='all'||destination.searchParams.get('threshold')!=='0.13'||destination.searchParams.get('split')!=='all')throw Error('assignment filters');
if(destination.searchParams.has('chip')||destination.searchParams.has('queue'))throw Error('stale chip route');
"""
    subprocess.run(['node','-e',script],check=True,capture_output=True,text=True)


def test_assignment_choices_include_prediction_imports_and_keep_owner(tmp_path):
    import hashlib
    from urban_tree_ml.review_assignments import assignment_choices, review_assignments
    from urban_tree_ml.review_models import model_choices, model_selector

    records = []
    examples = [
        ('v18', 'usbos', 'validation', False, 'Logan airport edges', True),
        ('runways', 'usbos', 'train', True, 'Logan east-south runways', True),
        ('sf', 'ussfo', 'validation', False, 'SF review', True),
        ('sealed', 'usbos', 'test', True, 'Sealed', True),
        ('partial', 'usbos', 'train', True, 'Incomplete', False),
    ]
    for family, city, split, prediction_only, queue, complete in examples:
        evaluation = tmp_path / 'runs' / family / 'evaluation' / (split + '-' + city)
        evaluation.mkdir(parents=True)
        if complete:
            (evaluation.parent.parent / 'COMPLETE').write_text('done')
        records.append(dict(run_id=family+'::'+evaluation.name, training_run_id=family,
                            city=city.upper(), created_at=family, evaluation_dir=evaluation,
                            metrics={'split': split, 'prediction_only': prediction_only}))
        directory = tmp_path / 'benchmarks' / family
        directory.mkdir(parents=True)
        target = directory / 'validation-targets.csv'
        target.write_text(f'city,chip_id,queue,reason\n{city},tile,{queue},Inspect\n')
        (directory / 'manifest.json').write_text(json.dumps({
            'new_run': family, 'output_sha256': {target.name: hashlib.sha256(target.read_bytes()).hexdigest()}}))
        (directory / 'COMPLETE').write_text('done')
    choices = assignment_choices(tmp_path, records, ['usbos'])
    assert {item['queue'] for item in choices} == {'Logan airport edges', 'Logan east-south runways'}
    runway = next(item for item in choices if item['queue'] == 'Logan east-south runways')
    assert runway['run_id'] == 'runways::train-usbos'
    assert len(assignment_choices(tmp_path, records, ['usbos', 'ussfo'])) == 3
    assert review_assignments(tmp_path, 'runways', 'USBOS')['chips']['tile']['queue'] == runway['queue']
    selectable = model_choices(records, 'usbos')
    assert {record['training_run_id'] for record in selectable} == {'v18', 'runways'}
    assert selectable[0]['training_run_id'] == 'v18'
    html = model_selector('<div class="controls"></div></body>', selectable, runway['run_id'])
    assert '<option value="runways::train-usbos" selected>' in html
    # A damaged assignment manifest cannot advertise a queue.
    (tmp_path / 'benchmarks/runways/validation-targets.csv').write_text('changed')
    assert {item['queue'] for item in assignment_choices(tmp_path, records, ['usbos'])} == {'Logan airport edges'}


def test_geographic_queue_spans_years_without_borrowing_predictions(tmp_path):
    import hashlib
    from urban_tree_ml.review_assignments import geographic_assignments, unpredicted_assignment_rows
    from urban_tree_ml.queue_prefetch import QUEUE_PREFETCH_JS
    queue = 'Logan east-south runways'
    chip = 'r000056_c000103'
    snapshot = tmp_path/'benchmarks/runways'
    snapshot.mkdir(parents=True)
    source = snapshot/'validation-targets.csv'
    source.write_text(f'city,chip_id,queue,reason\nusbos,{chip},{queue},Review\n')
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    (snapshot/'manifest.json').write_text(json.dumps(dict(new_run='old',output_sha256={source.name:digest})))
    (snapshot/'COMPLETE').touch()
    (tmp_path/'qa').mkdir()
    contexts, records, bundles, scopes = {}, [], {}, {}
    for year in (2023, 2025, 2021):
        acquisition = f'usbos-naip-{year}'
        directory = tmp_path/acquisition
        directory.mkdir()
        raster = directory/'imagery.vrt'
        raster.write_text(acquisition)
        scopes[acquisition] = dict(raster_sha256=hashlib.sha256(raster.read_bytes()).hexdigest(),chip_pixels=256)
        config = SimpleNamespace(paths=SimpleNamespace(root=tmp_path),imagery=SimpleNamespace(chip_pixels=256))
        owner = SimpleNamespace(city='usbos',config=config,raster=raster,directory=directory,acquisition_id=acquisition)
        contexts['usbos' if year==2023 else acquisition] = owner
        (directory/'manifest.json').write_text(json.dumps(dict(scenes=[dict(
            scene_id='scene-705',validation_chip_id=chip,splits=['train'],valid_fraction=1.,image='images/705.png') ])))
        if year==2021:
            continue
        run = f'v20::train-{acquisition}'
        records.append(dict(run_id=run,training_run_id='v20',city='USBOS',metrics={'split':'train'},
            bundle_config=SimpleNamespace(imagery=SimpleNamespace(acquisition_id=None if year==2023 else acquisition))))
        bundles[run] = SimpleNamespace(training_run_id='v20',config=SimpleNamespace(inventory=SimpleNamespace(city='USBOS')),
            chips=[dict(chip_id=chip)],summary=lambda:dict(city='USBOS',display={}))
    registry = tmp_path/'qa/geographic-review-assignments.json'
    registry.write_text(json.dumps(dict(assignments=[dict(city='usbos',queue=queue,split='train',snapshot='runways',
        source_sha256=digest,acquisitions=scopes)])))
    catalog = SimpleNamespace(records=lambda:records,bundle=lambda key:bundles[key])
    result = combined_summary(catalog, records[0]['run_id'],contexts['usbos'],contexts,all_imagery=True,scope_city='usbos')
    assert len(result['chips'])==3
    assert len(result['assignments']['chips'])==3
    assert all(a['queue']==queue for a in result['assignments']['chips'].values())
    pending = next(c for c in result['chips'] if c['acquisition_id'].endswith('2021'))
    assert pending['inference_run'] is None and pending['missed'] is None
    assert pending['scene_id']=='scene-705'
    script = 'const state='+json.dumps(result)+';\n'
    script += "const location={origin:'http://localhost',pathname:'/model',search:'?acquisition=all&city=usbos&compare_run=old'};\n"
    script += ALL_REVIEW_JS.split('async function initAllReview')[0]
    script += 'function queueTarget'+QUEUE_PREFETCH_JS.split('function queueTarget')[1].split('let queuePrefetchTimer')[0]
    script += """
const c=state.chips.find(c=>c.scene_id),u=new URL(allChipRoute(c.key,'q'),location.origin);
if(u.pathname!='/registration'||u.searchParams.get('scene')!=='scene-705'||u.searchParams.get('acquisition')!=='usbos-naip-2021'||u.searchParams.get('run'))throw Error('wrong unpredicted route');
const storage={length:0,setItem:(key,value)=>storage.value=JSON.parse(value)};
saveAllReviewQueue(storage,'q',state.chips,'usbos','/model');
const pageParameters=u.searchParams,curationReturn='/model';
const context={id:'q',queue:storage.value};
const index=state.chips.indexOf(c),next=new URL(queueDestination(context,index),location.origin);
if(next.pathname!='/registration'||next.searchParams.get('scene')!=='scene-705'||next.searchParams.get('run')||next.searchParams.has('compare_run'))throw Error('wrong queue route');
const predicted=new URL(queueDestination(context,0),location.origin);
if(predicted.pathname!='/curate-training'||!predicted.searchParams.get('run')||predicted.searchParams.has('acquisition'))throw Error('original route retained stale year');
"""
    subprocess.run(['node','-e',script],check=True,capture_output=True,text=True)
    owner=contexts['usbos-naip-2021']
    manifest=owner.directory/'manifest.json'
    data=json.loads(manifest.read_text());data['scenes'][0]['splits']=['test'];manifest.write_text(json.dumps(data))
    assert unpredicted_assignment_rows(owner)==([], {})
    data['scenes'][0]['splits']=['train'];data['scenes'][0]['valid_fraction']=.5;manifest.write_text(json.dumps(data))
    assert unpredicted_assignment_rows(owner)==([], {})
    owner.city='ussfo'
    assert geographic_assignments(owner)=={}
    owner.city='usbos';owner.raster.write_text('different grid')
    assert geographic_assignments(owner)=={}
    source.write_text('tampered')
    assert geographic_assignments(contexts['usbos'])=={}
