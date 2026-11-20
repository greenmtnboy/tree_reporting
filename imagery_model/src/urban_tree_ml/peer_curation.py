"""Preview human tree curation from the nearest dated development image.

No writes, model predictions, completion flags, or test labels are copied.
The caller applies tree decisions and clipped area annotations through normal saves.
"""
import hashlib
import math
from datetime import date
from pyproj import Transformer
from urban_tree_ml.curation_admission import human_review

AREA_MODES = {'confirmed-background','protect','confirmed-woods','single-tree'}


def _peer_areas(target, scene, peer):
    """Project and clip areas to this development tile; never import adjoining land."""
    from shapely.geometry import Point, Polygon
    from shapely.ops import transform
    from shapely import make_valid
    from urban_tree_ml.tree_curation import geographic_regions
    crs=target['manifest']['metadata']['curation_crs']
    forward=Transformer.from_crs('EPSG:4326',crs,always_xy=True)
    inverse=Transformer.from_crs(crs,'EPSG:4326',always_xy=True)
    samples={str(s['sample_id']):s for s in target['manifest']['samples']}
    anchor=scene.get('anchor') or next(samples[str(i)] for i in scene['sample_ids'] if str(i) in samples)
    x,y=forward.transform(anchor['longitude'],anchor['latitude'])
    def corner(px,py):
        dx,dy=px-anchor['target_x'],py-anchor['target_y']
        return (x+anchor['transform_a']*dx+anchor['transform_b']*dy,
                y+anchor['transform_d']*dx+anchor['transform_e']*dy)
    width,height=scene['image_width'],scene['image_height']
    footprint=Polygon([corner(0,0),corner(width,0),corner(width,height),corner(0,height)])
    source_crs=peer['manifest']['metadata']['curation_crs']
    source_project=Transformer.from_crs('EPSG:4326',source_crs,always_xy=True)
    to_target=Transformer.from_crs(source_crs,crs,always_xy=True)
    selected=[r for r in peer['state'].get('mask_regions',[]) if r.get('mode') in AREA_MODES and human_review(r)]
    regions=[]
    for original in geographic_regions(selected,peer['manifest']):
        if 'polygon' in original:
            shape=transform(forward.transform,Polygon(original['polygon']))
        else:
            sx,sy=source_project.transform(original['longitude'],original['latitude'])
            shape=transform(to_target.transform,Point(sx,sy).buffer(original['radius_m'],quad_segs=32))
        # Freehand editor masks use even/odd filling. Linework repair retains
        # their drawn edges and separates crossed lobes before tile clipping.
        if not shape.is_valid: shape=make_valid(shape)
        clipped=shape.intersection(footprint)
        if clipped.is_empty or clipped.area<1e-8: continue
        parts=[clipped] if clipped.geom_type=='Polygon' else [p for p in getattr(clipped,'geoms',[]) if p.geom_type=='Polygon']
        for index,part in enumerate(sorted(parts,key=lambda p:(p.bounds,p.wkb_hex))):
            if part.interiors: raise ValueError('Peer area holes are unsupported')
            coords=list(part.exterior.coords)[:-1]
            center=part.representative_point()
            lon,lat=inverse.transform(center.x,center.y)
            lons,lats=inverse.transform(*zip(*coords))
            radius=max(math.hypot(px-center.x,py-center.y) for px,py in coords)+1e-6
            rid=hashlib.sha256(f"{target['id']}:{peer['id']}:area:{original['region_id']}:{scene['scene_id']}:{index}".encode()).hexdigest()[:32]
            regions.append(dict(region_id=rid,mode=original['mode'],longitude=lon,latitude=lat,
                                radius_m=radius,polygon=list(map(list,zip(lons,lats))),source='peer-image:'+peer['id']))
    return regions


def _frame(manifest, scene):
    samples={str(s['sample_id']):s for s in manifest['samples']}
    anchor=scene.get('anchor') or next((samples[str(i)] for i in scene['sample_ids'] if str(i) in samples),None)
    if anchor is None: raise ValueError('Peer copy requires a georeferenced scene anchor')
    project=Transformer.from_crs('EPSG:4326',manifest['metadata']['curation_crs'],always_xy=True)
    x,y=project.transform(anchor['longitude'],anchor['latitude'])
    a,b,d,e=[float(anchor['transform_'+k]) for k in 'abde']
    det=a*e-b*d
    if not math.isfinite(det) or det==0: raise ValueError('Invalid scene transform')
    def pixel(lon,lat):
        wx,wy=project.transform(lon,lat); dx,dy=wx-x,wy-y
        return anchor['target_x']+(e*dx-b*dy)/det,anchor['target_y']+(-d*dx+a*dy)/det
    return project,pixel


def _curated_trees(peer):
    manifest,state=peer['manifest'],peer['state']
    crs=manifest['metadata']['curation_crs']
    forward=Transformer.from_crs('EPSG:4326',crs,always_xy=True)
    inverse=Transformer.from_crs(crs,'EPSG:4326',always_xy=True)
    seen=set()
    for sample in manifest['samples']:
        tid=str(sample['tree_id']);review=state.get('reviews',{}).get(str(sample['sample_id']),{})
        # Explicit human confirmation only. A tile completion flag alone cannot
        # turn an inventory or model suggestion into a peer-confirmed tree.
        overlap = review.get('source') == 'crown-overlap' and review.get('status') == 'occluded'
        if tid in seen or not (human_review(review) or overlap) or review.get('status') not in {'aligned','offset','occluded','not-tree','duplicate','uncertain'}: continue
        if sample.get('split') not in {'train','validation'}: continue
        lon,lat=sample['longitude'],sample['latitude']
        if 'east_m' in review or 'north_m' in review:
            x,y=forward.transform(lon,lat);lon,lat=inverse.transform(x+review.get('east_m',0),y+review.get('north_m',0))
        elif 'image_x' in review:
            dx,dy=review['image_x']-sample['target_x'],review['image_y']-sample['target_y']
            x,y=forward.transform(lon,lat)
            lon,lat=inverse.transform(x+sample['transform_a']*dx+sample['transform_b']*dy,y+sample['transform_d']*dx+sample['transform_e']*dy)
        seen.add(tid)
        yield dict(tree_id=tid,longitude=lon,latitude=lat,split=sample['split'],
                   status=review['status'],crown_radius_m=review.get('crown_radius_m'),
                   species_override=review.get('species_override'),
                   overlap=overlap,heuristic_id=review.get('heuristic_id'))
    for region in state.get('mask_regions',[]):
        if region.get('mode')!='confirmed-tree' or region.get('tree_id') or not human_review(region): continue
        if 'longitude' in region:
            lon,lat=region['longitude'],region['latitude']
        elif 'world_x' in region:
            lon,lat=inverse.transform(region['world_x'],region['world_y'])
        else: continue
        yield dict(tree_id='manual-'+region['region_id'],longitude=lon,latitude=lat,
                   crown_radius_m=region['radius_m'],species=region.get('species'),split=None)


def preview_peer_trees(target, scene_id, peers):
    """Use one nearest overlapping curated acquisition; ties prefer older dates.

    Registry entries bind an identical reference-grid split policy. This allows
    manual geographic trees to inherit the destination footprint's split without
    reassigning geography. Differing development splits are retained as import
    provenance, not a reason to block editing or to reassign destination splits.
    """
    scene=next(s for s in target['manifest']['scenes'] if s['scene_id']==scene_id)
    split=scene.get('split')
    if split is None and len(scene.get('splits',[]))==1: split=scene['splits'][0]
    if split not in {'train','validation'}: raise ValueError('Peer copy needs one development split; holdouts/mixed scenes excluded')
    project,pixel=_frame(target['manifest'],scene)
    width,height=scene['image_width'],scene['image_height']
    target_date=date.fromisoformat(target['date'][:10])
    ordered=sorted((p for p in peers if p['city']==target['city'] and p['id']!=target['id']),
        key=lambda p:(abs((date.fromisoformat(p['date'][:10])-target_date).days),p['date'],p['id']))
    selected=None;trees=[];areas=[]
    for peer in ordered:
        if peer['split_policy_sha256']!=target['split_policy_sha256']: continue
        candidates=[]
        for tree in _curated_trees(peer):
            x,y=pixel(tree['longitude'],tree['latitude'])
            if not (0<=x<width and 0<=y<height): continue
            candidates.append(tree)
        candidate_areas=_peer_areas(target,scene,peer)
        if candidates or candidate_areas: selected,trees,areas=peer,candidates,candidate_areas;break
    if selected is None: return {'source':None,'reviews':{},'mask_regions':[],'copied':0,'skipped':0}
    samples=[s for s in target['manifest']['samples'] if s.get('scene_id')==scene_id]
    by_tree={str(s['tree_id']):s for s in samples}
    current=target['state'];reviews={};regions=[];occupied=[];skipped=0
    for tree in _curated_trees(target):
        if tree.get('status') not in {'not-tree','duplicate','uncertain','occluded'}:
            occupied.append(project.transform(tree['longitude'],tree['latitude']))
    source='peer-image:'+selected['id']
    for tree in trees:
        provenance = ({'peer_source_split':tree['split'], 'peer_target_split':split}
                      if tree['split'] is not None and tree['split'] != split else {})
        x,y=project.transform(tree['longitude'],tree['latitude'])
        sample=by_tree.get(tree['tree_id'])
        existing_review=current.get('reviews',{}).get(str(sample['sample_id'])) if sample else None
        if (existing_review and (existing_review.get('source')!='inventory-suggestion'
                or set(existing_review)-{'status','source'})) or (not sample and any(math.hypot(x-a,y-b)<1 for a,b in occupied)):
            skipped+=1;continue
        if sample:
            ox,oy=project.transform(sample['longitude'],sample['latitude'])
            review={'status':tree.get('status','offset'),'east_m':x-ox,'north_m':y-oy,'source':source,**provenance}
            if tree.get('overlap'):
                # Retain advisory provenance rather than promoting a copied
                # overlap suggestion to human-confirmed training evidence.
                review.update(source='crown-overlap',peer_image=selected['id'])
                if tree.get('heuristic_id') is not None: review['heuristic_id']=tree['heuristic_id']
            if tree.get('crown_radius_m') is not None: review['crown_radius_m']=tree['crown_radius_m']
            if tree.get('species_override') is not None: review['species_override']=tree['species_override']
            reviews[str(sample['sample_id'])]=review
        else:
            if tree.get('status') in {'not-tree','duplicate','uncertain'}:
                skipped+=1;continue
            # Never invent a crown target for an unmatched center-only inventory
            # tree. New manual trees always carry a human-drawn radius.
            if tree.get('crown_radius_m') is None: skipped+=1;continue
            rid=hashlib.sha256(f"{target['id']}:{selected['id']}:{tree['tree_id']}".encode()).hexdigest()[:32]
            if any(r.get('region_id')==rid for r in current.get('mask_regions',[])): skipped+=1;continue
            region=dict(region_id=rid,mode='confirmed-tree',world_x=x,world_y=y,
                        radius_m=tree['crown_radius_m'],source=source,**provenance)
            if tree.get('species'): region['species']=tree['species']
            regions.append(region)
        if tree.get('status') not in {'not-tree','duplicate','uncertain','occluded'}:
            occupied.append((x,y))
    existing_ids={r['region_id'] for r in current.get('mask_regions',[])}
    for area in areas:
        if area['region_id'] in existing_ids: skipped+=1
        else: regions.append(area)
    return dict(source={'id':selected['id'],'date':selected['date']},reviews=reviews,
                mask_regions=regions,copied=len(reviews)+len(regions),skipped=skipped,
                completion_flags_copied=False,clear_target_completion=True)


def preview_peer_revert(target, scene_id, peers):
    """Reset unchanged imports in this tile to inventory defaults.

    Legacy imports have no before-image journal. Require exact canonical
    agreement with a reconstructed import, preserving subsequent edits.
    """
    from copy import deepcopy
    from urban_tree_ml.feedback import normalize_review_payload, normalize_mask_region_payload
    from urban_tree_ml.tree_curation import tree_records, expand_tree_records
    blank=deepcopy(target);blank['state']={'reviews':{},'mask_regions':[],'scene_reviews':{}}
    reviews={};regions=[];sources=[]
    for peer in peers:
        expected=preview_peer_trees(blank,scene_id,[peer])
        if not expected['source']: continue
        payload={'reviews':expected['reviews'],'mask_regions':expected['mask_regions']}
        canonical_reviews=normalize_review_payload(payload,target['manifest'],migrate_crowns=False)
        canonical_regions=normalize_mask_region_payload(payload,target['manifest'])
        # Saved tree positions round geographic coordinates to ten decimals.
        # Reproduce that exact storage conversion instead of broadening the
        # tolerance used to protect subsequent user edits.
        persisted_reviews=normalize_review_payload({'reviews':expand_tree_records(
            tree_records(canonical_reviews,target['manifest']),target['manifest'])},
            target['manifest'],migrate_crowns=False)
        for sid,value in canonical_reviews.items():
            current_review=target['state'].get('reviews',{}).get(sid)
            if _same_import(current_review,value) or _same_import(current_review,persisted_reviews.get(sid)):
                reviews[sid]={'status':'aligned','source':'inventory-suggestion'}
        current={r['region_id']:r for r in target['state'].get('mask_regions',[])}
        for region in canonical_regions:
            if _same_import(current.get(region['region_id']),region): regions.append(region['region_id'])
        sources.append(peer['id'])
    return dict(reviews=reviews,remove_mask_regions=regions,reverted=len(reviews)+len(regions),
                sources=sources,mode='unchanged-imports-to-inventory',completion_flags_restored=False)


def _same_import(current, expected):
    """Only tolerate sub-micrometre projection roundoff, never edited fields."""
    if not isinstance(current,dict) or current.keys()!=expected.keys(): return False
    for key,value in expected.items():
        if key in {'east_m','north_m','world_x','world_y','longitude','latitude'}:
            tolerance=1e-12 if key in {'longitude','latitude'} else 1e-6
            if not math.isclose(current[key],value,rel_tol=0,abs_tol=tolerance): return False
        elif current[key]!=value: return False
    return True
