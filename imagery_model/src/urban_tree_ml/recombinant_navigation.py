"""Resolve a synthetic pixel to its real, guarded source curation viewport."""
import json
import math
from urllib.parse import urlencode

import numpy as np
import rasterio
from pyproj import Transformer
from rasterio.transform import Affine

from urban_tree_ml import recombinant as r
from urban_tree_ml.recombinant_labels import make_reference


def with_latest_predictions(scope, target, context, catalog, compatible):
    """Pick the newest completed, imagery-compatible TRAIN evaluation, not a UI default.

    Regular evaluated windows keep chip IDs stable across 30/60cm derivatives.
    Panned cuts can open the evaluated window containing the actual clicked point.
    Never invent predictions or silently fall back to an older model for coverage.
    """
    from types import SimpleNamespace
    from urban_tree_ml.chip_geometry import chip_offsets
    records=sorted(catalog.records(),key=lambda v:(v['created_at'],v['run_id']),reverse=True)
    result=dict(target,run_id=None,prediction_status='No completed compatible model predictions are available.')
    for record in records:
        if (record['city'].lower()!=context.city or record['metrics'].get('split')!='train'
            or not (record['evaluation_dir'].parent.parent/'COMPLETE').is_file()):continue
        candidate=SimpleNamespace(config=record['bundle_config'],raster_path=record['raster_path'])
        try:
            if not compatible(context,candidate):continue
        except (OSError,ValueError):continue
        try:
            bundle=catalog.bundle(record['run_id'])
        except (OSError,ValueError,KeyError):
            result['prediction_status']=f"Latest model {record['training_run_id']} prediction files are unavailable. Source curation remains available."
            return result
        src=r.source(scope)
        world=Affine(*src['transform'][:6])*(target['source_x'],target['source_y'])
        with rasterio.open(bundle.raster_path) as raster:
            xy=Transformer.from_crs(src['crs'],raster.crs,always_xy=True).transform(*world)
            col,row=~raster.transform*xy
        size=bundle.config.imagery.chip_pixels
        hits=[]
        for chip in bundle.chip_ids:
            if chip.startswith('shift_'):continue
            r0,c0=chip_offsets(chip,size)
            if c0<=col<c0+size and r0<=row<r0+size:
                hits.append(chip)
        if not hits:
            result['prediction_status']=f"Latest model {record['training_run_id']} has no saved predictions covering this point. Source curation remains available."
            return result
        chip=sorted(hits)[0]
        manifest=json.loads((context.directory/'manifest.json').read_text())
        scene=next((s for s in manifest['scenes'] if s.get('validation_chip_id')==chip),None)
        return dict(result,run_id=record['run_id'],chip_id=chip,
                    scene_id=scene['scene_id'] if scene else None,
                    prediction_status='Predictions: '+record['training_run_id'])
    return result


def locate(scope, generated_id, x, y, context):
    x,y=float(x),float(y)
    if not (math.isfinite(x) and math.isfinite(y) and 0<=x<512 and 0<=y<512):
        raise ValueError('Choose a point inside the generated chip')
    src=r.source(scope)
    if src['city'] != context.city:
        raise ValueError('Source city does not match curation workspace')
    make_reference(scope,context.directory,context.raster,
                   context.config.paths.root/'inventory'/context.city/'inventory.parquet')
    recipe=next((v for v in r.state(scope)['generated'] if v['id']==generated_id),None)
    if recipe is None or recipe['source_id']!=src['source_id'] or recipe['source_sha256']!=src['source_sha256']:
        raise ValueError('Unknown generated chip or source identity mismatch')
    p=next((p for p in recipe['placements'] if p['x']<=x<p['x']+p['width'] and p['y']<=y<p['y']+p['height']),None)
    if p is None: raise ValueError('No source placement here')
    tiles=recipe['tiles']; ids={p['tile_id']}|{v['tile_id'] for v in r.background_parts(p,tiles)}
    masks={i:r.load_tile(scope,tiles[i])[1] for i in ids}
    for layer,tile,mask,dx,dy in r.placement_layers(p,tiles,masks):
        lx,ly=x-p['x']-dx,y-p['y']-dy
        if 0<=lx<mask.shape[1] and 0<=ly<mask.shape[0] and mask[int(ly),int(lx)]:
            break
    else: raise ValueError('No visible source layer here')
    _,entry,_=r.checked_window(scope,tile['chip'])
    if ((tile['source_x'],tile['source_y'])!=(entry['x']+tile['x'],entry['y']+tile['y'])
        or tile['crs']!=src['crs'] or tile['source_transform'][:6]!=src['transform'][:6]):
        raise ValueError('Saved tile source footprint differs')
    sx,sy=tile['source_x']+lx,tile['source_y']+ly
    wx,wy=Affine(*src['transform'][:6])*(sx,sy)
    manifest=json.loads((context.directory/'manifest.json').read_text())
    samples={s['sample_id']:s for s in manifest.get('samples',[])}
    projector=Transformer.from_crs('EPSG:4326',src['crs'],always_xy=True)
    matches=[]
    if manifest['metadata'].get('curation_crs')==src['crs']:
        for scene in manifest.get('scenes',[]):
            anchors=[samples[sid] for sid in scene['sample_ids'] if sid in samples] or [scene.get('anchor')]
            footprints=[]
            for a in anchors:
                if not a: continue
                ax,ay=projector.transform(a['longitude'],a['latitude'])
                t=Affine(a['transform_a'],a['transform_b'],0,a['transform_d'],a['transform_e'],0)
                ox,oy=t*(a['target_x'],a['target_y'])
                footprints.append((ax-ox,ay-oy,*tuple(t)[:6]))
            if not footprints or not np.allclose(footprints,footprints[0],atol=1e-4,rtol=0):continue
            ox,oy,*t=footprints[0]; cx,cy=~Affine(*t)*(wx-ox,wy-oy)
            if 0<=cx<scene['image_width'] and 0<=cy<scene['image_height']:
                matches.append(scene)
    result=dict(tile_id=tile['id'],name=tile['name'],layer=layer,source_x=sx,source_y=sy,
                source_window=tile['chip'],scene_id=None,chip_id=None)
    if matches:
        # Prefer a broad viewport: nearby unlabeled trees need space for correction.
        result['scene_id']=max(matches,key=lambda s:s['image_width']*s['image_height'])['scene_id']
    else:
        if manifest['metadata'].get('curation_schema_version')!=2:
            raise ValueError('No existing curation scene here; an image-scoped curation workspace is required')
        # A photo-window ID is NOT a curation chip ID. Translate its actual footprint.
        with rasterio.open(context.raster) as raster:
            project=Transformer.from_crs(src['crs'],raster.crs,always_xy=True)
            photo=Affine(*src['transform'][:6]); inverse=~raster.transform
            corners=[inverse*project.transform(*(photo*(entry['x']+dx,entry['y']+dy)))
                     for dx,dy in [(0,0),(512,0),(0,512),(512,512)]]
            col,row=corners[0]; size=context.config.imagery.chip_pixels
            expected=[(round(col),round(row)),(round(col)+size,round(row)),
                      (round(col),round(row)+size),(round(col)+size,round(row)+size)]
            if not np.allclose(corners,expected,atol=1e-4,rtol=0) or col < -1e-4 or row < -1e-4 or col+size>raster.width+1e-4 or row+size>raster.height+1e-4:
                raise ValueError('Source viewport does not align with the curation raster')
        result['chip_id']=f'shift_r{round(row):06d}_c{round(col):06d}'
    result['url']='/api/recombinant/curate?'+urlencode(dict(source=src['source_id'],id=generated_id,x=x,y=y))
    return result
