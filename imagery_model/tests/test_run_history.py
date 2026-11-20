import subprocess
from datetime import date
from types import SimpleNamespace

from urban_tree_ml.model_debug import RUN_HISTORY_HTML, RunDebugCatalog
from urban_tree_ml.temporal_studio import history_imagery


def record(city='USBOS', acquisition=None, raster='/archive/imagery/usbos/2023/image.vrt', cutoff=None):
    imagery = SimpleNamespace(acquisition_id=acquisition, local_raster=raster,
                              planting_date_cutoff=cutoff, collection='naip')
    return dict(city=city, bundle_config=SimpleNamespace(imagery=imagery), dataset='fixture',
                run_id='model', training_run_id='model', cohort='validation-usbos',
                metrics={'split': 'validation'}, evaluation_dir='fixture', raster_path=raster)


def test_history_groups_explicit_and_legacy_acquisitions_consistently():
    original = history_imagery(record())
    explicit = history_imagery(record(acquisition='usbos-naip-2023'))
    temporal = history_imagery(record(acquisition='usbos-naip-2025'))
    assert original == explicit
    assert original['imagery_label'] == 'Boston · 2023'
    assert temporal['imagery_label'] == 'Boston · 2025'
    assert original['imagery_key'] != temporal['imagery_key']
    assert history_imagery(record('USFFX', raster='frozen/imagery.vrt', cutoff=date(2023, 10, 11)))['imagery_label'] == 'Fairfax · 2023'
    assert history_imagery(record('USNYC', raster='/archive/imagery/usnyc/2023/image.tif'))['imagery_label'] == 'New York · 2023'
    assert history_imagery(record('USSFO', raster='/archive/imagery/ussfo/2022/image.vrt'))['imagery_label'] == 'San Francisco · 2022'


def test_history_never_guesses_unknown_imagery_year():
    unknown = record(raster='frozen/imagery.vrt')
    first = history_imagery(unknown)
    unknown['dataset'] = 'other'
    second = history_imagery(unknown)
    assert first['imagery_label'] == 'Boston · Undated imagery'
    assert first['imagery_key'] != second['imagery_key']


def test_temporal_summary_never_borrows_native_city_sweep(monkeypatch):
    from urban_tree_ml import goal_metrics, sweep_history
    monkeypatch.setattr(goal_metrics, 'goal_metrics', lambda directory: {})
    monkeypatch.setattr(sweep_history, 'best_sweeps', lambda root, policy: {('model', 'usbos'): {'threshold': .13}})
    catalog = object.__new__(RunDebugCatalog)
    catalog.config = SimpleNamespace(paths=SimpleNamespace(root='fixture'))
    catalog.selected_run_id = 'model'
    native = record()
    temporal = record(acquisition='usbos-naip-2025')
    temporal.update(run_id='model::validation-usbos-naip-2025', cohort='validation-usbos-naip-2025')
    results = catalog._build_summary([native, temporal])['runs']
    assert results[0]['best_goal_sweep']['threshold'] == .13
    assert results[1]['best_goal_sweep'] is None
    assert results[1]['best_goal_sweeps'] == {}
    assert results[1]['acquisition_id'] == 'usbos-naip-2025'


def test_history_browser_grouping_and_species_sample_support():
    # Execute the actual served script to catch syntax and grouping regressions.
    script = RUN_HISTORY_HTML.split('<script>', 1)[1].split('</script>', 1)[0]
    script = script.replace('}init();', '}')
    harness = '''
const assert = require('node:assert/strict');
const elements = Object.fromEntries(['dataset','history-city','score-mode','radius','summary','rows','chart'].map(k=>[k,{value:'',innerHTML:''}]));
elements['score-mode'].value='fixed';elements.radius.value='2.0';
global.document={getElementById:k=>elements[k]};
'''
    checks = '''
const row=(year,samples,accuracy)=>({run_id:'model-'+year,training_run_id:'model',city:'USBOS',city_label:'Boston',imagery_key:'usbos-naip-'+year,imagery_label:'Boston · '+year,created_at:'2026-01-01',metrics:{split:'validation',confidence_threshold:.35,metrics_by_match_radius_m:{'2.0':{attributes_on_matched_detections:{species:{samples,accuracy,macro_f1:accuracy}}}}},goal_metrics:{'2.0':{detection:{precision:.5,recall:.5,f1:.5}}}});
catalog={runs:[row('2025',1,1),row('2023',83,.37)]};
assert.notEqual(comparisonKey(catalog.runs[0]),comparisonKey(catalog.runs[1]));
assert.equal(bestRuns(catalog.runs).size,2);
render();
assert.equal((elements.chart.innerHTML.match(/<h2>/g)||[]).length,2);
assert.ok(elements.chart.innerHTML.includes('Boston · 2023'));
assert.ok(elements.chart.innerHTML.includes('Boston · 2025'));
assert.ok(elements.chart.innerHTML.includes('n=1</text>'));
assert.ok(elements.rows.innerHTML.includes('n=1 matched labeled tree'));
assert.ok(elements.rows.innerHTML.includes('100.0%'));
assert.ok(elements.rows.innerHTML.includes('n=83 matched labeled trees'));
assert.ok(!speciesCell({samples:0,accuracy:1},'accuracy').includes('100.0%'));
assert.ok(speciesCell(undefined,'accuracy').includes('Sample count unavailable'));
elements['history-city'].value='USFFX';render();
assert.ok(elements.chart.innerHTML.includes('No evaluations'));
'''
    completed = subprocess.run(['node', '-e', harness + script + checks], capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
