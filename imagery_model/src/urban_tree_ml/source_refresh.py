"""Conservative SF training-only source refresh; never modifies live reviews."""
import ast
import hashlib
import json
import shutil
import types
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
from pyproj import Transformer

from urban_tree_ml.config import load_config
from urban_tree_ml.inventory import _add_source_label_eligibility
from urban_tree_ml.prepare_next_run import reencode
from urban_tree_ml.splits import assign_spatial_splits
from urban_tree_ml.taxonomy import Taxonomy


def merged_ids(value):
    if isinstance(value, str):
        if value.startswith('['):
            return [str(i) for i in json.loads(value)]
        return [i.strip() for i in value.split(',') if i.strip()]
    return [str(i) for i in value] if isinstance(value, (list, np.ndarray)) else []


def select_hybrid(old, new, old_safe, new_safe, protected_ids, alias_protected):
    """Keep edited/held-out/boundary-crossing identities; replace only safe SF IDs."""
    old = old.set_index('tree_id', drop=False)
    new = new.set_index('tree_id', drop=False)
    if not old.index.is_unique or not new.index.is_unique:
        raise ValueError('Duplicate primary IDs')
    eligible_old = set(old.index[old_safe]) - set(protected_ids)
    eligible_new = set(new.index[new_safe]) - set(protected_ids) - set(alias_protected)
    # Neither move a retained identity across boundaries nor delete its old row.
    shared = set(old.index) & set(new.index)
    blocked = {i for i in shared if i not in eligible_old or i not in eligible_new}
    eligible_old -= blocked
    eligible_new -= blocked
    kept = old.loc[~old.index.isin(eligible_old)]
    added = new.loc[new.index.isin(eligible_new),old.columns].copy()
    for column,dtype in old.dtypes.items():
        if pd.api.types.is_datetime64_any_dtype(dtype):
            added[column]=pd.to_datetime(added[column],errors='raise')
        added[column]=added[column].astype(dtype)
    result = pd.concat([kept, added], ignore_index=True)
    if result.tree_id.duplicated().any():
        raise ValueError('Hybrid creates duplicate IDs')
    return result, eligible_old, eligible_new


def prepare(root, base, policy, config_path):
    root, base = Path(root), Path(base)
    source = Path(policy['source'])
    if hashlib.sha256(source.read_bytes()).hexdigest() != policy['sha256']:
        raise ValueError('Pinned SF source changed')
    directory = root/'qa/registration/ussfo-2022-mosaic'
    raw = {n:(directory/n).read_bytes() for n in ('reviews.json','manifest.json')}
    state, manifest = (json.loads(raw[n]) for n in ('reviews.json','manifest.json'))
    config = load_config(config_path)
    old = pd.read_parquet(base/'ussfo/inventory.parquet')
    new = pd.read_parquet(source)
    # Preserve other sources, including manually added trees, outside this refresh.
    new = new[new.data_source.eq('SF_OPENDATA') & new.tree_id.str.startswith('sf-')].copy()
    new = new[np.isfinite(new.latitude) & np.isfinite(new.longitude)].copy()
    new = _add_source_label_eligibility(new, config.inventory)
    new = assign_spatial_splits(new, config.split, config.seed)
    info = json.loads((base/'preparation.json').read_text())
    normalizer = (Path(info['profile'])/'upstream_ingest_snapshot.py').read_bytes()
    if hashlib.sha256(normalizer).hexdigest() != info['normalizer_sha256']:
        raise ValueError('Pinned normalizer changed')
    module = types.ModuleType('pinned_normalizer')
    # The archived normalizer also re-exports a Trilogy output writer. We only
    # use its pure species functions, not that unrelated CLI emission dependency.
    tree = ast.parse(normalizer)
    tree.body = [node for node in tree.body if not (
        isinstance(node, ast.ImportFrom) and node.module == 'trilogy.io.arrow')]
    exec(compile(tree, 'pinned_normalizer', 'exec'), module.__dict__)
    taxonomy = Taxonomy(**json.loads((base/'ussfo/taxonomy.json').read_text()))
    new = reencode(new, taxonomy, module.sanitize_species)
    new['dbh_log1p'] = np.log1p(new.diameter_at_breast_height.where(new.dbh_eligible))
    samples = {s['sample_id']:s for s in manifest['samples']}
    records = state['tree_reviews']
    with rasterio.open(config.imagery.local_raster) as raster:
        transform, crs = raster.transform, raster.crs
    project = Transformer.from_crs('EPSG:4326', crs, always_xy=True)
    scene_forward = Transformer.from_crs('EPSG:4326', manifest['metadata']['curation_crs'], always_xy=True)
    scene_project = Transformer.from_crs(manifest['metadata']['curation_crs'], crs, always_xy=True)
    chips = pd.read_parquet(root/'chips'/policy['chip_dataset']/'chips.parquet')
    chips = chips[chips.split.eq('train')].copy()
    x,y = transform*(chips.column_offset.to_numpy(),chips.row_offset.to_numpy())
    xx,yy = transform*(chips.column_offset.to_numpy()+config.imagery.chip_pixels,chips.row_offset.to_numpy()+config.imagery.chip_pixels)
    left,right,bottom,top = np.minimum(x,xx),np.maximum(x,xx),np.minimum(y,yy),np.maximum(y,yy)
    protected = np.zeros(len(chips),dtype=bool)
    def protect(l,b,r,t):
        nonlocal protected
        protected |= (right>l)&(left<r)&(top>b)&(bottom<t)
    for scene in manifest['scenes']:
        ss=[samples[i] for i in scene['sample_ids']]
        done=state.get('scene_reviews',{}).get(scene['scene_id'],{})
        if not ss or not (done.get('done') or done.get('more_done') or any(records.get(str(s['tree_id']),{}).get('source')=='human' for s in ss)):
            continue
        s=ss[0]; x,y=scene_forward.transform(s['longitude'],s['latitude'])
        a,b,d,e=[s.get('transform_'+k,0) for k in ('a','b','d','e')]
        x-=a*s['target_x']+b*s['target_y'];y-=d*s['target_x']+e*s['target_y']
        corners=[scene_project.transform(x+a*u+b*v,y+d*u+e*v) for u,v in [(0,0),(scene['image_width'],0),(0,scene['image_height']),(scene['image_width'],scene['image_height'])]]
        protect(min(z[0] for z in corners),min(z[1] for z in corners),max(z[0] for z in corners),max(z[1] for z in corners))
    for s in samples.values():
        v=records.get(str(s['tree_id']))
        if v is None:continue
        for lon,lat in [(s['longitude'],s['latitude']),(v.get('corrected_longitude',s['longitude']),v.get('corrected_latitude',s['latitude']))]:
            x,y=project.transform(lon,lat);r=max(float(v.get('crown_radius_m',0)),.01);protect(x-r,y-r,x+r,y+r)
    for v in list(state.get('added_trees',{}).values())+state.get('mask_regions',[]):
        x,y=project.transform(v['longitude'],v['latitude']);r=float(v.get('crown_radius_m',v.get('radius_m',0)))+.01;protect(x-r,y-r,x+r,y+r)
    safe_offsets=set(zip(chips.loc[~protected,'row_offset'],chips.loc[~protected,'column_offset']))
    def safe(frame):
        x,y=project.transform(frame.longitude.to_numpy(),frame.latitude.to_numpy());col,row=(~transform)*(x,y)
        size=config.imagery.chip_pixels
        in_chip=np.array([(int(np.floor(r/size))*size,int(np.floor(c/size))*size) in safe_offsets for r,c in zip(row,col)])
        return in_chip & frame.split.eq('train').to_numpy() & frame.split_eligible.to_numpy() & frame.tree_id.str.startswith('sf-').to_numpy()
    # If any merged identity is protected, retain its old source row, don't duplicate it.
    alias_protected=set()
    old_safe=safe(old)
    kept_ids=set(old.tree_id[~old_safe]) | set(records)
    for row in new.to_dict('records'):
        values=merged_ids(row.get('merged_tree_ids'))
        if any(i in kept_ids and i!=row['tree_id'] for i in values):alias_protected.add(row['tree_id'])
    result, removed, inserted = select_hybrid(old,new,old_safe,safe(new),records,alias_protected)
    pd.testing.assert_frame_equal(old[old.split.ne('train')].reset_index(drop=True),result[result.split.ne('train')].reset_index(drop=True))
    preserved=old[old.tree_id.isin(records)].set_index('tree_id').sort_index()
    pd.testing.assert_frame_equal(preserved,result[result.tree_id.isin(preserved.index)].set_index('tree_id').sort_index())
    if any((directory/n).read_bytes()!=v for n,v in raw.items()):raise ValueError('Curation changed during preparation; retry')
    output=root/'prepared'/('sf-source-pilot-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    output.mkdir(parents=True)
    for city in ('ussfo','usbos'):
        (output/city).mkdir();shutil.copy2(base/city/'taxonomy.json',output/city/'taxonomy.json')
    shutil.copy2(base/'usbos/inventory.parquet',output/'usbos/inventory.parquet')
    result.to_parquet(output/'ussfo/inventory.parquet',index=False)
    audit=dict(policy=policy,old_rows=len(old),new_rows=len(result),safe_train_chips=len(safe_offsets),removed=len(removed-inserted),updated=len(removed&inserted),added=len(inserted-removed),protected_curation_rows=len(preserved),validation_test_unchanged=True,boston_unchanged=True,curation_sha256=hashlib.sha256(raw['reviews.json']).hexdigest())
    (output/'preparation.json').write_text(json.dumps({**info,'source_refresh':audit},indent=2))
    pd.DataFrame({'tree_id':sorted(removed|inserted)}).to_csv(output/'changed-ids.csv',index=False)
    print(json.dumps({'prepared':str(output),**audit},indent=2),flush=True)
    return output
