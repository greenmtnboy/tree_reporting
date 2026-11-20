"""Explicit immutable train-only recombinant datasets. Never launches training."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import pandas as pd
import rasterio
from pyproj import Transformer
from rasterio.transform import Affine
from rasterio.windows import Window

from urban_tree_ml import recombinant as r
from urban_tree_ml import recombinant_labels as linkage
from urban_tree_ml.boundary_shifts import digest, training_footprint
from urban_tree_ml.config import normalization_path
from urban_tree_ml.feedback import load_persisted_reviews, _json_sha256
from urban_tree_ml.targets import PointLabel, DetectionMaskRegion, build_targets


def stable_reviews(scope, resolved):
    directory = linkage.resolve_path(linkage.artifact_root(scope), resolved['reference']['review_dir'])
    captured = {n: (directory/n).read_bytes() for n in ('manifest.json', 'reviews.json', 'training-feedback.json')}
    if captured['training-feedback.json'] != resolved['_inputs']['training-feedback.json']:
        raise ValueError('Curation changed during export; retry')
    with TemporaryDirectory(prefix='recombinant-review-check-') as temporary:
        for name, data in captured.items():
            (Path(temporary)/name).write_bytes(data)
        state = load_persisted_reviews(temporary)
    manifest = json.loads(captured['manifest.json'])
    feedback = json.loads(captured['training-feedback.json'])
    if (feedback.get('source_reviews_sha256') != state['state_revision'] or
        feedback.get('source_manifest_sha256') != _json_sha256(manifest)):
        raise ValueError('Publish current curation before export')
    if any((directory/n).read_bytes() != data for n, data in captured.items()):
        raise ValueError('Curation changed during export; retry')
    return manifest, state, captured


def reviewed_rectangles(manifest, state, photo):
    """Recover exact scene footprints, not a full-chip claim from a small crop."""
    if manifest['metadata'].get('curation_crs') != photo['crs']:
        raise ValueError('Reviewed coverage currently requires matching photo/curation CRS')
    projector = Transformer.from_crs('EPSG:4326', photo['crs'], always_xy=True)
    inverse = ~Affine(*photo['transform'][:6])
    samples = {str(v['sample_id']): v for v in manifest['samples']}
    rectangles = []
    for scene in manifest['scenes']:
        review = state['scene_reviews'].get(scene['scene_id'], {})
        if scene.get('splits') != ['train'] or not (review.get('done') or review.get('more_done')):
            continue
        anchors = [samples[sid] for sid in scene['sample_ids'] if sid in samples]
        if not anchors:
            continue  # No verified geographic anchor, never infer from a chip ID.
        footprints = []
        for a in anchors:
            if a['transform_b'] or a['transform_d'] or a['transform_a'] <= 0 or a['transform_e'] >= 0:
                raise ValueError('Reviewed scene must be north-up')
            wx, wy = projector.transform(a['longitude'], a['latitude'])
            left = wx-a['transform_a']*a['target_x']
            top = wy-a['transform_e']*a['target_y']
            x0,y0 = inverse*(left, top)
            x1,y1 = inverse*(left+a['transform_a']*scene['image_width'],
                             top+a['transform_e']*scene['image_height'])
            footprints.append((x0,y0,x1,y1))
        if not np.allclose(footprints, footprints[0], atol=1e-4, rtol=0):
            raise ValueError('Conflicting reviewed scene footprints')
        rectangles.append(footprints[0])
    return rectangles


def covered_pixels(tile, rectangles):
    w,h = r.dimensions(tile)
    covered = np.zeros((h,w), dtype=bool)
    for left, top, right, bottom in rectangles:
        # Include only whole pixels; tolerate projection roundoff, not partial coverage.
        x0=max(0,int(np.ceil(left-tile['source_x']-1e-5)))
        y0=max(0,int(np.ceil(top-tile['source_y']-1e-5)))
        x1=min(w,int(np.floor(right-tile['source_x']+1e-5)))
        y1=min(h,int(np.floor(bottom-tile['source_y']+1e-5)))
        if x1>x0 and y1>y0:
            covered[y0:y1,x0:x1]=True
    return covered


def verify_split_policy(root, photo, config):
    policy=photo.get('split_policy')
    if policy is None:
        # Legacy source manifests name the immutable run that admitted their windows.
        run=photo.get('parent_run')
        if not run:
            raise ValueError('Source has no pinned split policy or parent run')
        path=linkage.resolve_path(root,f'runs/{run}/evaluation/train-{photo["city"]}/evaluation-metadata.json')
        original=json.loads(path.read_text())['config']
        policy=dict(seed=original['seed'],split=original['split'])
    if policy!=dict(seed=config.seed,split=config.split.model_dump(mode='json')):
        raise ValueError('Export split policy differs from pinned source policy')


def local_regions(resolved, photo, tile):
    # Same metric, north-up 30cm grid; transform polygons, centers and radii together.
    source_affine = Affine(*resolved['transform'][:6])
    if (resolved['crs'] != photo['crs'] or source_affine.b or source_affine.d
        or source_affine.a <= 0 or source_affine.e >= 0
        or not np.isclose(source_affine.a, -source_affine.e)):
        raise ValueError('Region transfer requires matching CRS and square north-up pixels')
    regions=[]
    for value in resolved['regions']:
        x,y=linkage.transform_xy(value['pixel_col'],value['pixel_row'],resolved['transform'],resolved['crs'],photo)
        points=value.get('polygon', [])
        if points:
            p=np.asarray(points)
            px,py=linkage.transform_xy(p[:,0],p[:,1],resolved['transform'],resolved['crs'],photo)
            points=tuple(zip(px-tile['source_x'],py-tile['source_y']))
        regions.append(DetectionMaskRegion(x=x-tile['source_x'], y=y-tile['source_y'],
            radius=value['radius_px']*source_affine.a/.3, mode=value['mode'], polygon=tuple(points)))
    return regions


def overlap_box(visible, x, y, radius):
    h,w=visible.shape
    x0,y0=int(np.floor(x-radius)),int(np.floor(y-radius))
    x1,y1=int(np.ceil(x+radius))+1,int(np.ceil(y+radius))+1
    area=visible[max(0,y0):min(h,y1),max(0,x0):min(w,x1)] if x1>0 and y1>0 and x0<w and y0<h else []
    intersects=bool(np.any(area))
    contained=x0>=0 and y0>=0 and x1<=w and y1<=h and bool(np.all(area))
    return intersects, contained


def layer_targets(tile, pixels, visible, resolved, photo, config, blend_width):
    """Rebuild canonical targets per visible layer, then zero uncertain seam cells."""
    h,w=visible.shape; stride=config.targets.output_stride
    safe = r.feather_alpha(visible, blend_width) == 1 if blend_width else visible.copy()
    regions=local_regions(resolved,photo,tile)
    frame=resolved['frame']
    xs,ys=linkage.transform_xy(frame.pixel_col.to_numpy(),frame.pixel_row.to_numpy(),
                             resolved['transform'],resolved['crs'],photo)
    points=[]; rows=[]; ignored=[]; outside_ignores=[]
    for pos,row in enumerate(frame.itertuples(index=False)):
        x,y=float(xs[pos]-tile['source_x']),float(ys[pos]-tile['source_y'])
        if row.planted_after_imagery:
            continue
        radius=getattr(row,'crown_radius_m',None)
        known=radius is not None and np.isfinite(radius) and radius>0
        if row.feedback_excluded:
            # Includes centers outside the cut whose ignore area reaches visible pixels.
            if 0<=x<w and 0<=y<h:
                ignored.append((x,y))
            else:
                outside_ignores.append(DetectionMaskRegion(x,y,config.targets.positive_supervision_radius_m/.3,'protect'))
            continue
        if not known:
            if 0<=x<w and 0<=y<h and visible[int(y),int(x)]:
                raise ValueError(f'Unknown crown extent for {row.tree_id}; review before export')
            continue
        intersects, contained=overlap_box(visible,x,y,float(radius)/.3)
        if not intersects:
            continue
        if not contained or not overlap_box(safe,x,y,float(radius)/.3)[1]:
            raise ValueError(f'Clipped/occluded crown or blend-band overlap: {row.tree_id}')
        p=PointLabel(x,y,dbh_log1p=float(row.dbh_log1p) if row.dbh_eligible else None,
                     genus_id=int(row.genus_id) if row.genus_eligible else None,
                     species_id=int(row.species_id) if row.species_eligible else None,
                     crown_radius_m=float(radius), crown_weight=float(getattr(row,'crown_weight',1.)))
        points.append(p)
        rows.append(dict(source_tree_id=str(row.tree_id),x=x,y=y,dbh_log1p=p.dbh_log1p,
                         genus_id=p.genus_id,species_id=p.species_id,crown_radius_m=p.crown_radius_m,
                         vegetation_type=str(getattr(row,'vegetation_type','') or '')))
    # Group polygons can extend beyond their circular radius: do not silently clip them.
    for region in regions:
        if region.mode not in {'copse','shrub-mass'}:
            continue
        if region.polygon:
            p=np.asarray(region.polygon); x0,y0=p.min(axis=0);x1,y1=p.max(axis=0)
            intersects,contained=overlap_box(visible,(x0+x1)/2,(y0+y1)/2,max(x1-x0,y1-y0)/2)
            safe_contained=overlap_box(safe,(x0+x1)/2,(y0+y1)/2,max(x1-x0,y1-y0)/2)[1]
        else:
            intersects,contained=overlap_box(visible,region.x,region.y,region.radius)
            safe_contained=overlap_box(safe,region.x,region.y,region.radius)[1]
        if intersects and (not contained or not safe_contained):
            raise ValueError('Clipped vegetation group or blend-band overlap')
    image=pixels.astype(np.float32)*config.imagery.input_scale
    denominator=image[3]+image[0]
    ndvi=np.divide(image[3]-image[0],denominator,out=np.zeros_like(image[0]),where=np.abs(denominator)>1e-6)
    t=config.targets
    arrays=build_targets(h,w,points,stride=stride,gaussian_sigma_px=t.gaussian_sigma_px,
        supervision_radius_px=t.positive_supervision_radius_m/.3,valid_mask=safe,ndvi=ndvi,
        mask_regions=outside_ignores+regions,ignored_locations=ignored,background_mode=t.background_mode,
        background_ndvi_max=t.background_ndvi_max,collision_policy=t.collision_policy,
        crown_scaled_center=t.crown_scaled_center,resolution_m=.3,
        crown_center_fraction=t.crown_center_fraction,crown_center_max_sigma_m=t.crown_center_max_sigma_m,
        crown_estimated_scale=t.crown_estimated_scale)
    allowed=safe.reshape(h//stride,stride,w//stride,stride).all(axis=(1,3))
    # Canonical positives can override region ignores, but not absent/uncertain pixels.
    for key in ('center','detection_mask','scoring_mask','dbh_mask','genus_mask','species_mask',
                'crown_mask','single_tree_groups'):
        arrays[key]=np.where(allowed,arrays[key],0)
    from urban_tree_ml.targets import find_collision_groups, point_label_output_cell
    collisions=find_collision_groups(points,image_height=h,image_width=w,stride=stride)
    discarded={i for ids in collisions.values() for i in ids}
    retained=[]
    for i,(p,row) in enumerate(zip(points,rows)):
        cell=point_label_output_cell(p,image_height=h,image_width=w,stride=stride)
        if i not in discarded:
            if not allowed[cell[1],cell[0]]:
                raise ValueError('Positive center falls in an uncertain output cell')
            retained.append(row|dict(output_x=cell[0],output_y=cell[1]))
    return arrays,retained,allowed


def materialize(recipe, tiles, assets, resolved, photo, config, rectangles):
    stride=config.targets.output_stride
    if stride<=0 or 16%stride or config.imagery.chip_pixels!=512 or config.imagery.resolution_m!=.3:
        raise ValueError('Export requires 512px/30cm and an output stride dividing 16')
    if not resolved['frame']['split'].eq('train').all() or resolved['frame'].tree_id.duplicated().any():
        raise ValueError('Canonical source labels must be unique and train-only')
    if recipe.get('review_status')!='seams-accepted':
        raise ValueError('Accept generated chip seams before export')
    occupied=np.zeros((512,512),dtype=bool)
    raw=np.zeros((4,512,512),dtype=np.uint8)
    result={}; rows=[]; group_offset=0; collisions=0; discarded=0; layer_cache={}
    band=recipe.get('blending',{}).get('width_px',0)
    masks={identifier:asset[1] for identifier,asset in assets.items()}
    if type(band) is not int or band not in (0,2,4,8):
        raise ValueError('Unsupported blend width')
    for index,p in enumerate(recipe['placements']):
        x,y,w,h=(p[k] for k in ('x','y','width','height'))
        if (any(type(v) is not int or v%16 for v in (x,y,w,h)) or w<=0 or h<=0
            or x<0 or y<0 or x+w>512 or y+h>512 or occupied[y:y+h,x:x+w].any()):
            raise ValueError('Invalid, overlapping or misaligned placement')
        occupied[y:y+h,x:x+w]=True
        fg=tiles[p['tile_id']]; pixels,mask=assets[fg['id']]
        if r.dimensions(fg)!=(w,h):
            raise ValueError('Placement dimensions differ')
        raw[:,y:y+h,x:x+w]=pixels
        if p.get('background_id'):
            backing=r.backing_pixels(p,tiles,lambda identifier:assets[identifier],band)
            alpha=r.feather_alpha(mask,band)[None,:,:]
            raw[:,y:y+h,x:x+w]=np.clip(np.rint(pixels*alpha+backing*(1-alpha)),0,255).astype(np.uint8)
        elif not mask.all():
            raise ValueError('Unfilled irregular cut')
        for layer,tile,visible,dx,dy in r.placement_layers(p,tiles,masks):
            if not visible.any():
                continue
            if (tile['source_id']!=photo['source_id'] or tile['source_sha256']!=photo['source_sha256']
                or not tile.get('borders_clear')):
                raise ValueError('Missing clear-border confirmation or mismatched source')
            if not covered_pixels(tile,rectangles)[visible].all():
                raise ValueError('Visible source pixels lack completed training curation coverage')
            cache_key=(tile['id'],hashlib.sha256(visible.tobytes()).hexdigest(),band)
            if cache_key not in layer_cache:
                layer_cache[cache_key]=layer_targets(tile,assets[tile['id']][0],visible,resolved,photo,config,band)
            cached,records,allowed=layer_cache[cache_key]
            arrays=dict(cached,single_tree_groups=cached['single_tree_groups'].copy())
            collisions+=int(arrays.pop('collision_cells')); discarded+=int(arrays.pop('collision_excluded_points'))
            groups=arrays['single_tree_groups']; groups[groups>0]+=group_offset
            group_offset=int(groups.max(initial=group_offset))
            tw,th=r.dimensions(tile)
            tx,ty=x+dx,y+dy
            sy,sx=slice(ty//stride,(ty+th)//stride),slice(tx//stride,(tx+tw)//stride)
            for key,a in arrays.items():
                if key not in result:
                    result[key]=np.full((512//stride,512//stride),-1 if key in ('species','genus') else 0,dtype=a.dtype)
                target=result[key][sy,sx]
                target[allowed]=a[allowed]
            for row in records:
                rows.append(row|dict(tree_id=f'{recipe["id"]}:{index}:{layer}:{row["source_tree_id"]}',
                    chip_id=recipe['id'],split='train',placement=index,layer=layer,tile_id=tile['id'],
                    x=row['x']+tx,y=row['y']+ty,output_x=row['output_x']+tx//stride,output_y=row['output_y']+ty//stride))
    if not occupied.all():
        raise ValueError('Generated layout has gaps')
    if recipe.get('packing')=='mixed-rect-v1':
        image=r.blend_rectangles(raw,recipe['placements'],band)
    elif recipe.get('packing','uniform')=='uniform':
        image=r.blend_grid(raw,recipe['size'],band)
    else:
        raise ValueError('Unsupported packing recipe')
    if hashlib.sha256(image.tobytes()).hexdigest()!=recipe['pixel_sha256']:
        raise ValueError('Reconstructed image differs from saved generated chip')
    result['image']=image.astype(np.float32)*config.imagery.input_scale
    if not result['detection_mask'].any():
        raise ValueError('No supervised output cells remain')
    return result,rows,dict(collision_cell_count=collisions,collision_excluded_count=discarded)


def verify_export(directory):
    """Check a completed export before a future training job consumes it."""
    directory=Path(directory).resolve()
    if not (directory/'COMPLETE').is_file():
        raise ValueError('Synthetic export is incomplete')
    provenance=json.loads((directory/'provenance.json').read_text())
    if provenance.get('resolver')!='recombinant-export-v1' or provenance.get('train_only') is not True:
        raise ValueError('Unknown synthetic export policy')
    actual={p.relative_to(directory).as_posix() for p in directory.rglob('*') if p.is_file()}
    expected=set(provenance['files'])|{'COMPLETE','provenance.json'}
    if actual!=expected:
        raise ValueError('Export files differ from integrity manifest')
    for name,sha in provenance['files'].items():
        if digest(linkage.resolve_path(directory,name))!=sha:
            raise ValueError('Export checksum mismatch: '+name)
    manifest=pd.read_parquet(directory/'chips.parquet')
    if manifest.empty or set(manifest.split)!={'train'} or manifest.chip_id.duplicated().any():
        raise ValueError('Export must have unique train-only chips')
    summary=json.loads((directory/'summary.json').read_text())
    if summary.get('training_ready') is not True or summary.get('test_evaluated') is not False:
        raise ValueError('Export not admitted for training')
    return dict(chips=len(manifest),files=len(expected),verified=True)


def export_dataset(scope, generated_ids, config, destination):
    """Create a new train-only dataset, never modify sources or append to a run."""
    root=linkage.artifact_root(scope); destination=Path(destination).resolve()
    destination.relative_to(root/'recombinant-exports')
    if destination==root/'recombinant-exports' or destination.exists():
        raise ValueError('Choose a new export directory under recombinant-exports')
    if not generated_ids or len(set(generated_ids))!=len(generated_ids):
        raise ValueError('Choose distinct saved generated chips')
    library=r.state(scope); photo=r.source(scope)
    verify_split_policy(root,photo,config)
    if config.imagery.bands!=[1,2,3,4] or not np.isclose(config.imagery.input_scale,1/255):
        raise ValueError('Export requires RGB+NIR band order and uint8/255 scaling')
    if photo['crs']!=config.split.projected_crs:
        raise ValueError('First export requires photo and split projected CRS to match')
    aff=Affine(*photo['transform'][:6])
    if not np.allclose([aff.a,aff.b,aff.d,aff.e],[.3,0,0,-.3]):
        raise ValueError('Only native north-up 30cm export is supported')
    resolved=linkage.resolve_published(scope,config,capture_inputs=True)
    manifest,state,captured=stable_reviews(scope,resolved)
    rectangles=reviewed_rectangles(manifest,state,photo)
    norm_bytes=normalization_path(config).read_bytes(); norm=json.loads(norm_bytes)
    if (np.asarray(norm['mean']).shape!=(4,) or np.asarray(norm['std']).shape!=(4,) or not np.isfinite(norm['mean']).all()
        or not np.isfinite(norm['std']).all() or not (np.asarray(norm['std'])>0).all()):
        raise ValueError('Invalid inherited four-band normalization')
    taxonomy=json.loads(resolved['_inputs']['taxonomy.json'])
    mapping=taxonomy.get('species_to_genus')
    if (not isinstance(mapping,list) or len(mapping)!=len(taxonomy['species'])
        or len(set(taxonomy['species']))!=len(taxonomy['species'])
        or len(set(taxonomy['genera']))!=len(taxonomy['genera'])
        or any(type(v) is not int or not -1<=v<len(taxonomy['genera']) for v in mapping)):
        raise ValueError('Invalid frozen taxonomy mapping')
    if digest(photo['raster'])!=photo['source_sha256']:
        raise ValueError('Source photograph checksum mismatch')
    by_id={v['id']:v for v in library.get('generated',[])}
    prepared=[]; recipes=[]
    with rasterio.open(photo['raster']) as source:
        if source.crs.to_string()!=photo['crs'] or list(source.transform)[:6]!=photo['transform'][:6]:
            raise ValueError('Source photograph georeferencing changed')
        for identifier in generated_ids:
            if identifier not in by_id:
                raise ValueError('Unknown generated chip')
            recipe=by_id[identifier]; tiles=recipe['tiles']; assets={}
            if recipe['source_id']!=photo['source_id'] or recipe['source_sha256']!=photo['source_sha256']:
                raise ValueError('Generated source identity mismatch')
            for tid,tile in tiles.items():
                if tid!=tile['id']:
                    raise ValueError('Tile ID mismatch')
                _,entry,_=r.checked_window(scope,tile['chip'])
                if not training_footprint(source,entry['x'],entry['y'],512,config):
                    raise ValueError('Source footprint touches validation/test or guard')
                if (tile['source_x'],tile['source_y'])!=(entry['x']+tile['x'],entry['y']+tile['y']):
                    raise ValueError('Cut source origin mismatch')
                if tile['crs']!=photo['crs'] or tile['source_transform'][:6]!=photo['transform'][:6]:
                    raise ValueError('Cut georeferencing mismatch')
                pixels,mask=r.load_tile(scope,tile); w,h=r.dimensions(tile)
                window=Window(tile['source_x'],tile['source_y'],w,h)
                if not np.array_equal(pixels,source.read([1,2,3,4],window=window)) or not source.read_masks([1,2,3,4],window=window).all():
                    raise ValueError('Cut pixels no longer match the valid source photograph')
                assets[tid]=(pixels,mask)
            arrays,rows,counts=materialize(recipe,tiles,assets,resolved,photo,config,rectangles)
            for row in rows:
                for key,values in (('species_id',taxonomy['species']),('genus_id',taxonomy['genera'])):
                    if row[key] is not None and not 0<=row[key]<len(values):
                        raise ValueError('Target ID outside frozen taxonomy')
                if row['species_id'] is not None and row['genus_id'] is not None and mapping[row['species_id']]!=row['genus_id']:
                    raise ValueError('Source target species and genus disagree')
            prepared.append((identifier,arrays,rows,counts)); recipes.append(recipe)
    # All admission checks pass before creating a destination. A sibling staging
    # directory makes failure/partial files impossible to mistake for a dataset.
    destination.parent.mkdir(parents=True,exist_ok=True)
    with TemporaryDirectory(prefix='.recombinant-export-',dir=destination.parent) as temporary:
        stage=Path(temporary)/'dataset'; (stage/'train').mkdir(parents=True)
        (stage/'inputs').mkdir(); chips=[]; all_rows=[]
        for identifier,arrays,rows,counts in prepared:
            np.savez_compressed(stage/'train'/(identifier+'.npz'),**arrays)
            chips.append(dict(chip_id=identifier,split='train',path=f'train/{identifier}.npz',
                              tree_count=len(rows),candidate_tree_count=len(rows)+counts['collision_excluded_count'],
                              synthetic=True,**counts))
            all_rows.extend(rows)
        pd.DataFrame(chips).to_parquet(stage/'chips.parquet',index=False)
        columns=['tree_id','source_tree_id','chip_id','split','placement','layer','tile_id','x','y','output_x','output_y',
                 'dbh_log1p','genus_id','species_id','crown_radius_m','vegetation_type']
        pd.DataFrame(all_rows,columns=columns).to_parquet(stage/'labels.parquet',index=False)
        for name,data in (resolved['_inputs']|captured).items():
            (stage/'inputs'/name).write_bytes(data)
        (stage/'normalization.json').write_bytes(norm_bytes)
        (stage/'taxonomy.json').write_bytes(resolved['_inputs']['taxonomy.json'])
        def write(name,value):
            (stage/name).write_text(json.dumps(value,indent=2,allow_nan=False),encoding='utf-8')
        write('config.json',config.model_dump(mode='json'))
        write('recipes.json',recipes); write('source.json',photo)
        write('curation-reference.json',resolved['reference'])
        write('summary.json',dict(chips=len(chips),trees=len(all_rows),training_ready=True,
              label_status='available',synthetic=True,train_only=True,test_evaluated=False,
              policy='reviewed-visible-source-v1; uncertain seam output cells excluded'))
        write('provenance.json',dict(schema_version=1,resolver='recombinant-export-v1',
              annotation_revision=resolved['revision'],library_revision=library['revision'],
              train_only=True,files={p.relative_to(stage).as_posix():digest(p) for p in stage.rglob('*') if p.is_file()}))
        (stage/'COMPLETE').write_text('Immutable synthetic training dataset; no training launched.\n')
        verify_export(stage)
        os.rename(stage,destination)  # Does not replace an existing directory on Windows.
    return dict(destination=str(destination),chips=len(chips),trees=len(all_rows),training_started=False)
