import json
import threading
from contextlib import contextmanager
from pathlib import Path
from queue import Queue
from types import SimpleNamespace
from urllib.request import Request, urlopen
import pytest

from urban_tree_ml.config import load_config
from urban_tree_ml.temporal_studio import inject_acquisition_controls


def test_acquisition_assets_and_api_routing_are_isolated(tmp_path, monkeypatch):
    from urban_tree_ml import qa_server as qa
    from http.server import ThreadingHTTPServer
    contexts=[]
    for key,marker in [('base','original'),('alternate','different')]:
        directory=tmp_path/key;directory.mkdir()
        (directory/'marker.txt').write_text(marker)
        manifest={'metadata':{'review_id':key,'source_raster':str(tmp_path/(key+'.tif'))},
                  'samples':[], 'scenes':[]}
        (directory/'manifest.json').write_text(json.dumps(manifest))
        (directory/'reviews.json').write_text(json.dumps({'metadata':manifest['metadata'],
            'reviews':{},'scene_reviews':{},'mask_regions':[]}))
        cfg=load_config(Path(__file__).parents[1]/'configs/boston_naip_external.yaml');cfg.paths.root=tmp_path
        contexts.append(qa.ReviewContext('usbos',key,cfg,tmp_path/(key+'.tif'),directory,tmp_path/'missing',key))
    class Snapshots:
        def __init__(self,*args): self.snapshot_lock=threading.Lock()
        def request(self,*args): pass
        def status(self,*args): return {}
        def flush(self,*args): pass
        def close(self): pass
    monkeypatch.setattr(qa,'SnapshotWorker',Snapshots)
    monkeypatch.setattr(qa,'BackupJob',lambda **kwargs:None)
    class Catalog:
        def __init__(self,*args):pass
        def __len__(self):return 0
        def records(self):return []
    monkeypatch.setattr(qa,'RunDebugCatalog',Catalog)
    ready=Queue()
    def server(address,handler):
        instance=ThreadingHTTPServer(address,handler);ready.put(instance);return instance
    monkeypatch.setattr(qa,'ThreadingHTTPServer',server)
    thread=threading.Thread(target=qa._serve_review_contexts,args=({'usbos':contexts[0]},),
        kwargs=dict(default_city='usbos',bind='127.0.0.1',port=0,acquisitions={'alternate':contexts[1]}),daemon=True)
    thread.start();instance=ready.get(timeout=10)
    base=f'http://127.0.0.1:{instance.server_port}'
    try:
        assert urlopen(base+'/city-assets/usbos/marker.txt').read()==b'original'
        assert urlopen(base+'/city-assets/alternate/marker.txt').read()==b'different'
        assert urlopen(base+'/city-assets/base/marker.txt').read()==b'original'
        request=Request(base+'/api/reviews?city=usbos',headers={'X-Curation-Acquisition':'alternate'})
        data=json.load(urlopen(request))
        assert data['metadata']['review_id']=='alternate'
        assert json.load(urlopen(base+'/api/reviews?city=usbos'))['metadata']['review_id']=='base'
    finally:
        instance.shutdown();thread.join(timeout=10)
        assert not thread.is_alive()


def test_editor_pins_acquisition_requests_without_an_extra_header():
    context=SimpleNamespace(city='usbos',acquisition_id='usbos-2021')
    records={'usbos-2021':dict(id='usbos-2021',city='usbos',label='Boston 2021')}
    html=inject_acquisition_controls('<head></head><body></body>',context,records)
    assert 'Imagery acquisition' not in html and '<select' not in html
    assert 'X-Curation-Acquisition' in html and '"usbos-2021"' in html
    assert "url.origin===location.origin" in html


def test_temporal_raw_inventory_cannot_enter_training(tmp_path):
    from urban_tree_ml.chips import build_chips
    cfg=load_config(Path(__file__).parents[1]/'configs/boston_naip_external.yaml')
    cfg.paths.root=tmp_path;cfg.imagery.acquisition_id='usbos-naip-2021'
    with pytest.raises(ValueError,match='frozen curated admission'):
        build_chips(cfg,tmp_path/'unused.tif')
    assert not (tmp_path/'chips').exists()


def test_normal_chip_review_resolves_the_same_year_across_splits():
    from urban_tree_ml.temporal_studio import resolve_cohort, acquisition_records
    def record(year, split):
        return dict(run_id=f'v20::{split}-{year or "original"}', training_run_id='v20',
                    city='usbos', metrics={'split':split},
                    bundle_config=SimpleNamespace(imagery=SimpleNamespace(acquisition_id=year)))
    rows=[record(year,split) for year in (None,'usbos-naip-2021','usbos-naip-2025')
          for split in ('train','validation')]
    assert resolve_cohort(rows,'v20::validation-usbos-naip-2025','usbos','train')=='v20::train-usbos-naip-2025'
    assert resolve_cohort(rows,'v20::train-usbos-naip-2025','usbos','train')=='v20::train-usbos-naip-2025'
    assert resolve_cohort(rows,'v20::train-original','usbos','validation')=='v20::validation-original'
    assert resolve_cohort(rows,'v20::train-original','usbos','train','usbos-naip-2025')=='v20::train-usbos-naip-2025'
    assert len(acquisition_records(rows,'usbos-naip-2025'))==2
    assert not resolve_cohort(rows,'v20::train-original','ussfo','train','usbos-naip-2025')


def test_model_imagery_selector_stays_on_chip_review():
    from urban_tree_ml.temporal_studio import inject_model_acquisitions
    owner=SimpleNamespace(acquisition_id='usbos-naip-2025')
    records={'usbos-naip-2025':dict(city='usbos',label='Boston 2025')}
    rows=[dict(training_run_id='v20',run_id='v20::train-usbos-2025',metrics={'split':'train'},
          bundle_config=SimpleNamespace(imagery=SimpleNamespace(acquisition_id='usbos-naip-2025')))]
    html=inject_model_acquisitions('<div class="controls"></div></body>',owner,records,rows,'v20::train-usbos-2025')
    assert 'review-imagery' in html and 'Boston 2025' in html
    assert '/registration' not in html
    assert 'location.href=u.pathname+u.search' in html
    assert '"run": "v20::train-usbos-2025"' in html


def test_tile_labels_include_city_year_and_stable_geographic_id():
    from urban_tree_ml.temporal_studio import tile_label
    assert tile_label('usbos','r000056_c000103','usbos-naip-2025') == 'Boston · 2025 · r000056_c000103'
    assert tile_label('ussfo','r1_c2','ussfo-naip-2022') == 'San Francisco · 2022 · r1_c2'
    assert tile_label('usffx','r1_c2') == 'Fairfax · r1_c2'


def test_combined_review_does_not_merge_same_chip_from_other_year(tmp_path):
    from urban_tree_ml.all_review import combined_summary
    records=[];bundles={}
    for year in (None,'usbos-naip-2025'):
        config=SimpleNamespace(inventory=SimpleNamespace(city='USBOS'))
        identity=SimpleNamespace(imagery=SimpleNamespace(acquisition_id=year))
        for split in ('train','validation'):
            key=f'v20::{split}-{year or "original"}'
            records.append(dict(run_id=key,training_run_id='v20',city='USBOS',
                metrics={'split':split},bundle_config=identity))
            bundles[key]=SimpleNamespace(config=config,training_run_id='v20',
                chips=[{'chip_id':split}],summary=lambda:dict(city='USBOS',display={}))
    context=SimpleNamespace(config=SimpleNamespace(paths=SimpleNamespace(root=tmp_path)),
                            raster=tmp_path/'image.vrt',acquisition_id='usbos-naip-2025')
    catalog=SimpleNamespace(records=lambda:records,bundle=lambda key:bundles[key])
    result=combined_summary(catalog,'v20::train-usbos-naip-2025',context)
    assert len(result['chips'])==2
    assert all(c['inference_run'].endswith('usbos-naip-2025') for c in result['chips'])
    assert all(c['display_label'].startswith('Boston · 2025 · ') for c in result['chips'])


def test_collected_temporal_prediction_uses_frozen_graph_proof(tmp_path):
    import hashlib
    import xml.etree.ElementTree as ET
    import rasterio
    from affine import Affine
    from rasterio.transform import from_origin
    from rasterio.shutil import copy as raster_copy
    from rasterio.vrt import WarpedVRT
    from rasterio.enums import Resampling
    from prepare_temporal_training import portable_raster
    from urban_tree_ml.temporal_studio import matches_prediction_raster

    photo = tmp_path/'photo.tif'
    with rasterio.open(photo,'w',driver='GTiff',width=4,height=4,count=4,dtype='uint8',
                       crs='EPSG:32619',transform=from_origin(1,10,.6,.6)): pass
    original = tmp_path/'imagery.vrt'
    raster_copy(photo, original, driver='VRT')
    frozen = tmp_path/'frozen'
    frozen.mkdir()
    source = portable_raster(original, frozen, tmp_path, {}, {})
    target = frozen/'model.vrt'
    with rasterio.open(source) as src:
        with WarpedVRT(src, crs=src.crs, transform=src.transform*Affine.scale(.5),
                       width=8, height=8, resampling=Resampling.bilinear) as view:
            raster_copy(view, target, driver='VRT')
    digest = lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
    policy = dict(schema_version=1,test_evaluated=False,city='usbos',acquisition_id='usbos-naip-2025',
        source_raster=source.relative_to(tmp_path).as_posix(),model_raster=target.relative_to(tmp_path).as_posix(),
        checksums={p.relative_to(tmp_path).as_posix():digest(p) for p in (source,target)})
    policy_path = frozen/'temporal-admission.json'
    policy_path.write_text(json.dumps(policy))
    context = SimpleNamespace(city='usbos',acquisition_id='usbos-naip-2025',raster=original,
        config=SimpleNamespace(paths=SimpleNamespace(root=tmp_path),imagery=SimpleNamespace(chip_pixels=4)))
    bundle = SimpleNamespace(raster_path=target,config=SimpleNamespace(
        imagery=SimpleNamespace(acquisition_id='usbos-naip-2025',chip_pixels=8)))
    assert matches_prediction_raster(context,bundle)
    # Same footprint and filename do not make another city's acquisition valid.
    policy['city'] = 'usffx'; policy_path.write_text(json.dumps(policy))
    assert not matches_prediction_raster(context,bundle)
    policy['city'] = 'usbos'; policy_path.write_text(json.dumps(policy))
    original_bytes = original.read_bytes()
    tree = ET.parse(original)
    tree.find('.//SourceBand').text = '2'
    tree.write(original)
    assert not matches_prediction_raster(context,bundle)
    original.write_bytes(original_bytes)
    assert matches_prediction_raster(context,bundle)
    target.write_bytes(target.read_bytes()+b'\n')
    assert not matches_prediction_raster(context,bundle)
    policy_path.unlink()
    assert not matches_prediction_raster(context,bundle)


def test_temporal_prediction_raster_checks_year_and_bound_hashes(tmp_path):
    import hashlib
    import rasterio
    from rasterio.transform import from_origin
    from urban_tree_ml.temporal_studio import matches_prediction_raster
    source=tmp_path/'source.tif';target=tmp_path/'model.tif'
    for path,n,res in ((source,4,.6),(target,8,.3)):
        with rasterio.open(path,'w',driver='GTiff',width=n,height=n,count=4,dtype='uint8',
                           crs='EPSG:32619',transform=from_origin(1,10,res,res)): pass
    digest=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
    target.with_suffix('.provenance.json').write_text(json.dumps(dict(acquisition_id='usbos-naip-2025',
        source_sha256=digest(source),model_sha256=digest(target))))
    context=SimpleNamespace(acquisition_id='usbos-naip-2025',raster=source,
                            config=SimpleNamespace(imagery=SimpleNamespace(chip_pixels=4)))
    bundle=SimpleNamespace(raster_path=target,config=SimpleNamespace(imagery=SimpleNamespace(
        acquisition_id='usbos-naip-2025',chip_pixels=8)))
    assert matches_prediction_raster(context,bundle)
    context.acquisition_id='usbos-naip-2021'
    assert not matches_prediction_raster(context,bundle)
    context.acquisition_id='usbos-naip-2025'
    with source.open('ab') as f:f.write(b'changed')
    assert not matches_prediction_raster(context,bundle)


def test_all_imagery_keeps_duplicate_chip_ids_and_routes_separate(tmp_path):
    import subprocess
    from urban_tree_ml.all_review import combined_summary, ALL_REVIEW_JS
    from urban_tree_ml.queue_prefetch import QUEUE_PREFETCH_JS
    records=[];bundles={};contexts={}
    for city,acquisition in [('usbos',None),('usbos','usbos-naip-2025'),('ussfo','ussfo-naip-2024')]:
        owner=SimpleNamespace(config=SimpleNamespace(paths=SimpleNamespace(root=tmp_path)),
             raster=tmp_path/'image.vrt',acquisition_id=acquisition or 'usbos-naip-2023')
        contexts[acquisition or city]=owner
        config=SimpleNamespace(inventory=SimpleNamespace(city=city.upper()))
        key=f'v20::train-{acquisition or city}'
        records.append(dict(run_id=key,training_run_id='v20',city=city.upper(),metrics={'split':'train'},
            bundle_config=SimpleNamespace(imagery=SimpleNamespace(acquisition_id=acquisition))))
        bundles[key]=SimpleNamespace(config=config,training_run_id='v20',chips=[{'chip_id':'r000001_c000002'}],
            summary=lambda city=city:dict(city=city.upper(),display={},assignments={'chips':{'r000001_c000002':{'queue':'Review'}}}))
    catalog=SimpleNamespace(records=lambda:records,bundle=lambda key:bundles[key])
    selected=records[0]['run_id']
    result=combined_summary(catalog,selected,contexts['usbos'],contexts,all_imagery=True,scope_city='all')
    assert len(result['chips'])==3 and len({r['key'] for r in result['chips']})==3
    assert len(result['assignments']['chips'])==3
    assert len(result['review_scopes'])==3
    boston=combined_summary(catalog,selected,contexts['usbos'],contexts,all_imagery=True,scope_city='usbos')
    assert len(boston['chips'])==2
    assert {r['acquisition_filter'] for r in boston['chips']}=={'','usbos-naip-2025'}
    # A city's default review workspace can itself be a dated acquisition.
    primary_contexts = dict(contexts)
    primary_contexts['ussfo'] = primary_contexts.pop('ussfo-naip-2024')
    primary = combined_summary(catalog, selected, primary_contexts['ussfo'], primary_contexts,
                               all_imagery=True, scope_city='ussfo')
    assert [row['acquisition_filter'] for row in primary['chips']] == ['ussfo-naip-2024']
    code='const state='+json.dumps(result)+';\n'
    code+="const location={origin:'http://localhost',pathname:'/model',search:'?city=all&acquisition=all&split=all'};\n"
    code+=ALL_REVIEW_JS.split('async function initAllReview')[0]
    code+='''
const storage={length:0,setItem:(key,value)=>{storage.value=JSON.parse(value)}};
saveAllReviewQueue(storage,'q',state.chips,'all','/model?acquisition=all');
if(storage.value.version!==4)throw Error('temporal queue version');
for(const row of state.chips){
 const u=new URL(allChipRoute(row.key,'q'),location.origin);
 if((u.searchParams.get('acquisition')||'')!==row.acquisition_filter)throw Error('wrong acquisition');
 if(u.searchParams.get('run')!==row.inference_run)throw Error('wrong predictions');
}
const pageParameters=new URLSearchParams('city=usbos&acquisition=usbos-naip-2025&run=v20::train-usbos-naip-2025');
const curationReturn='/model?acquisition=all';
'''
    code+=QUEUE_PREFETCH_JS[QUEUE_PREFETCH_JS.index('function queueTarget'):QUEUE_PREFETCH_JS.index('let queuePrefetchTimer')]
    code+='''
const context={id:'q',queue:storage.value};
for(let i=0;i<state.chips.length;i++){
 const u=new URL(queueDestination(context,i),location.origin),row=state.chips[i];
 if((u.searchParams.get('acquisition')||'')!==row.acquisition_filter)throw Error('next year leaked');
 if(u.searchParams.get('city')!==row.city||u.searchParams.get('run')!==row.inference_run)throw Error('next cohort lost');
}
'''
    subprocess.run(['node','-e',code],check=True,capture_output=True,text=True)
