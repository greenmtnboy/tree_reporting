"""Optional, explicitly prepared acquisition registry for the curation studio."""
import json
import re
from dataclasses import replace
from html import escape
from pathlib import Path


def record_acquisition(record):
    config = record.get('bundle_config')
    return getattr(getattr(config, 'imagery', None), 'acquisition_id', None)


def history_imagery(record):
    """Describe saved evaluation imagery, including pre-acquisition-ID runs."""
    imagery = record['bundle_config'].imagery
    city = record['city'].lower()
    acquisition = imagery.acquisition_id
    source = str(imagery.local_raster or '').replace('\\', '/')
    year = None
    if acquisition:
        match = re.search(r'-(\d{4})$', acquisition)
        year = match.group(1) if match else None
    else:
        # The archive's imagery directory records the acquisition year. A STAC
        # search interval can cover many years and cannot identify the pixels.
        match = re.search(r'/imagery/' + re.escape(city) + r'/(\d{4})/', source)
        if match:
            year = match.group(1)
        elif imagery.planting_date_cutoff:
            year = str(imagery.planting_date_cutoff.year)
        if year:
            acquisition = f'{city}-{imagery.collection or "imagery"}-{year}'
    label = tile_label(city, '').rstrip(' ·')
    imagery_label = f'{label} · {year}' if year else f'{label} · {acquisition or "Undated imagery"}'
    return {
        'city_label': label,
        'acquisition_id': acquisition,
        'imagery_label': imagery_label,
        # Keep unidentified imagery datasets separate rather than guessing a year.
        'imagery_key': acquisition or f'{city}:undated:{record["dataset"]}',
    }


def matches_prediction_raster(context, bundle):
    """Verify a prepared temporal model-grid derivative without relaxing legacy guards."""
    import hashlib
    import rasterio
    acquisition = getattr(bundle.config.imagery, 'acquisition_id', None)
    if not acquisition or acquisition != context.acquisition_id:
        return False
    target = Path(bundle.raster_path)
    receipt_path = target.with_suffix('.provenance.json')
    digest = lambda path: hashlib.sha256(Path(path).read_bytes()).hexdigest()
    if receipt_path.is_file():
        receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
        if (receipt.get('acquisition_id') != acquisition or
                receipt.get('source_sha256') != digest(context.raster) or
                receipt.get('model_sha256') != digest(target)):
            return False
    elif not _matches_frozen_acquisition(context, target, acquisition):
        return False
    with rasterio.open(context.raster) as a, rasterio.open(target) as b:
        return (a.crs == b.crs and a.bounds == b.bounds and a.count == b.count == 4
                and all(abs(a.res[axis]*context.config.imagery.chip_pixels
                        - b.res[axis]*bundle.config.imagery.chip_pixels) < 1e-6 for axis in (0, 1)))


def _matches_frozen_acquisition(context, target, acquisition):
    """Accept collected training imagery only through its sealed acquisition policy.

    Training freezes relocate whole VRT graphs; they do not write the separate
    supplemental-inference provenance receipt. Verify their original proof.
    """
    import hashlib
    from urban_tree_ml.resolution_recipe import _raster_version, same_vrt_graph
    policy_path = target.parent/'temporal-admission.json'
    if not policy_path.is_file():
        return False
    try:
        root = Path(context.config.paths.root).resolve()
        policy = json.loads(policy_path.read_text(encoding='utf-8'))
        source = (root/policy['source_raster']).resolve()
        if (policy.get('schema_version') != 1 or policy.get('test_evaluated') is not False
                or policy['acquisition_id'] != acquisition or policy['city'] != context.city
                or (root/policy['model_raster']).resolve() != target.resolve()
                or not source.is_relative_to(root) or not target.resolve().is_relative_to(root)):
            return False
        # Every VRT in the model graph must still equal its frozen bytes.
        # The graph comparison below also proves the exact local leaf files.
        for name, *_ in _raster_version(target):
            path = Path(name)
            if path.suffix.lower() == '.vrt':
                relative = path.relative_to(root).as_posix()
                if hashlib.sha256(path.read_bytes()).hexdigest() != policy['checksums'].get(relative):
                    return False
        return same_vrt_graph(context.raster, source)
    except (OSError, ValueError, KeyError):
        return False


def acquisition_records(records, acquisition):
    """Prediction cohorts from different years must never merge by city alone."""
    if acquisition == 'all':
        return list(records)
    return [r for r in records if record_acquisition(r) == acquisition]


def tile_label(city, chip, acquisition=None):
    name = {'usbos':'Boston', 'ussfo':'San Francisco', 'usnyc':'New York', 'usffx':'Fairfax', 'uswas':'Washington, DC'}.get(city.lower(), city)
    year = acquisition.rsplit('-', 1)[-1] if acquisition else None
    parts = [name] + ([year] if year and re.fullmatch(r'\d{4}', year) else []) + [chip]
    return ' · '.join(parts)


def resolve_cohort(records, value, city, split, acquisition=None):
    candidates = [r for r in records if r['training_run_id'] == value.split('::')[0]
                  and r['city'].lower() == city and r['metrics'].get('split') == split]
    exact = next((r for r in candidates if r['run_id'] == value), None)
    if acquisition is None and exact is not None:
        return exact['run_id']
    if acquisition is None:
        source = next((r for r in records if r['run_id'] == value), None)
        acquisition = record_acquisition(source) if source else None
    candidates = acquisition_records(candidates, acquisition)
    return candidates[0]['run_id'] if candidates else ''


def inject_model_acquisitions(html, context, records, catalog_records, selected, scope=None):
    """Keep year selection on the normal model/chip-review page."""
    family = (selected or '').split('::')[0]
    options = ['<option value="all"'+(' selected' if scope == 'all' else '')+'>All imagery</option>',
               '<option value=""'+(' selected' if not scope else '')+'>Original imagery</option>']
    routes = {}
    original = next((r for r in catalog_records if r['training_run_id'] == family
                     and not record_acquisition(r) and r['city'].lower() == context.city
                     and r['metrics'].get('split') in ('train','validation')), None)
    if original:
        routes[''] = dict(city=original['city'].lower(),run=original['run_id'])
    for key, record in records.items():
        matches = [r for r in acquisition_records(catalog_records, key)
                   if r['training_run_id'] == family and r['metrics'].get('split') in ('train', 'validation')]
        if not matches:
            continue
        routes[key] = dict(city=record['city'], run=matches[0]['run_id'])
        options.append(f'<option value="{escape(key, quote=True)}"'
                       + (' selected' if scope == key else '')
                       + f'>{escape(record["label"])}</option>')
    control = '<label>Imagery <select id="review-imagery">' + ''.join(options) + '</select></label>'
    html = html.replace('<div class="controls">', '<div class="controls">' + control, 1)
    script = '''<script>
document.getElementById('review-imagery').onchange=e=>{
 const u=new URL(location.href), route=ROUTES[e.target.value];
 for(const k of ['chip','queue','queue_chip','compare_run','assignment'])u.searchParams.delete(k);
 if(e.target.value==='all'){u.searchParams.set('acquisition','all')}
 else if(route){if(e.target.value)u.searchParams.set('acquisition',e.target.value);else u.searchParams.delete('acquisition');u.searchParams.set('city',route.city);u.searchParams.set('run',route.run)}
 else {u.searchParams.delete('acquisition');u.searchParams.delete('run')}
 u.searchParams.set('split','all');location.href=u.pathname+u.search;
};</script>'''.replace('ROUTES', json.dumps(routes))
    return html.replace('</body>', script+'</body>')


def load_contexts(contexts, checked_context, load_config):
    root=next(iter(contexts.values())).config.paths.root
    path=root/'qa/temporal-acquisitions.json'
    if not path.exists(): return {},{}
    records=json.loads(path.read_text(encoding='utf-8'))['acquisitions']
    acquisitions={};metadata={}
    seen_ids=set(); seen_directories={}
    for record in records:
        key=record['id']
        if not re.fullmatch(r'[a-z0-9][a-z0-9-]*',key) or key in seen_ids: raise ValueError('Invalid/duplicate acquisition ID')
        seen_ids.add(key)
        if record['state']!='ready': continue
        config=load_config(record['config_path'])
        if config.paths.root.resolve()!=root.resolve(): raise ValueError('Acquisition artifact root differs')
        if config.imagery.acquisition_id not in (None,key): raise ValueError('Acquisition config identifies another year')
        context=checked_context(record['city'],record['label'],config,record['raster'],record['review_dir'],None)
        context=replace(context,acquisition_id=key)
        directory=context.directory.resolve()
        if directory in seen_directories: raise ValueError('Separate acquisitions share an annotation workspace')
        seen_directories[directory]=key
        metadata[key]=record
        original=contexts.get(record['city'])
        if original and original.directory==context.directory:
            contexts[record['city']]=replace(original,acquisition_id=key)
        else: acquisitions[key]=context
    return acquisitions,metadata


def inject_acquisition_controls(html, context, records):
    # Imagery is selected in chip review; the editor title already identifies
    # this tile's year. Keep only request ownership, without a second header.
    if context.acquisition_id not in records: return html
    # Every scene/gallery request is pinned to its acquisition, even if legacy
    # JavaScript constructs a fresh API URL. Headers never leave this origin.
    if context.acquisition_id:
        setup='''<script>
(()=>{const id=ACQUISITION, original=window.fetch.bind(window);
window.fetch=(input,options={})=>{
 const url=new URL(typeof input==='string'?input:input.url,location.href);
 if(url.origin===location.origin&&url.pathname.startsWith('/api/')){
  const headers=new Headers(input instanceof Request?input.headers:options.headers);
  if(options.headers)new Headers(options.headers).forEach((v,k)=>headers.set(k,v));
  headers.set('X-Curation-Acquisition',id);options={...options,headers};
 }
 return original(input,options);
};})();
</script>'''.replace('ACQUISITION',json.dumps(context.acquisition_id))
        html=html.replace('<head>','<head>'+setup,1)
    return html


def peer_preview(context, scene, all_contexts, records, *, revert=False):
    from urban_tree_ml.feedback import load_persisted_reviews
    from urban_tree_ml.peer_curation import preview_peer_trees, preview_peer_revert
    def entry(owner):
        record=records[owner.acquisition_id]
        return dict(id=owner.acquisition_id,city=owner.city,date=record['date'],
            split_policy_sha256=record['split_policy_sha256'],
            manifest=json.loads((owner.directory/'manifest.json').read_text(encoding='utf-8')),
            state=load_persisted_reviews(owner.directory))
    if context.acquisition_id not in records: raise ValueError('No dated peer imagery registered for this workspace')
    target=entry(context)
    peers=[entry(c) for c in all_contexts.values() if c.city==context.city and c.acquisition_id in records and c.acquisition_id!=context.acquisition_id]
    return (preview_peer_revert if revert else preview_peer_trees)(target,scene,peers)
