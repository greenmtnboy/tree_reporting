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
"""
    subprocess.run(['node','-e',script],check=True,capture_output=True,text=True)
