"""Image-scoped, tree-keyed corrections and geographic area masks (schema 2)."""
from copy import deepcopy
from functools import lru_cache

from pyproj import Transformer


@lru_cache(maxsize=16)
def transformers(crs):
    return (Transformer.from_crs('EPSG:4326',crs,always_xy=True),
            Transformer.from_crs(crs,'EPSG:4326',always_xy=True))


def tree_records(reviews, manifest):
    samples = {str(s['sample_id']): s for s in manifest['samples']}
    result = {}
    for sid, value in reviews.items():
        sample = samples[sid]
        record = dict(value)
        if 'image_x' in record and 'east_m' not in record:
            dx, dy = record['image_x']-sample['target_x'], record['image_y']-sample['target_y']
            record['east_m'] = sample['transform_a']*dx + sample['transform_b']*dy
            record['north_m'] = sample['transform_d']*dx + sample['transform_e']*dy
        record.pop('image_x', None)
        record.pop('image_y', None)
        if record.get('status') in {'offset','occluded'} and 'east_m' in record and manifest['metadata'].get('curation_crs'):
            crs=manifest['metadata']['curation_crs']
            forward,inverse=transformers(crs)
            x,y=forward.transform(sample['longitude'],sample['latitude'])
            lon,lat=inverse.transform(x+record.pop('east_m'),y+record.pop('north_m'))
            record['corrected_longitude'],record['corrected_latitude']=round(lon,10),round(lat,10)
        tid = str(sample['tree_id'])
        if tid in result and result[tid] != record:
            raise ValueError(f'Conflicting curation for tree {tid}; resolve before migration/save')
        result[tid] = record
    return result


def expand_tree_records(records, manifest):
    result={}
    projector=Transformer.from_crs('EPSG:4326',manifest['metadata']['curation_crs'],always_xy=True) if manifest['metadata'].get('curation_crs') else None
    for sample in manifest['samples']:
        if str(sample['tree_id']) not in records:continue
        record=deepcopy(records[str(sample['tree_id'])])
        if 'corrected_longitude' in record:
            x,y=projector.transform(record.pop('corrected_longitude'),record.pop('corrected_latitude'))
            ox,oy=projector.transform(sample['longitude'],sample['latitude'])
            record['east_m'],record['north_m']=x-ox,y-oy
        result[str(sample['sample_id'])]=record
    return result


def geographic_regions(regions, manifest):
    crs = manifest['metadata']['curation_crs']
    forward = Transformer.from_crs('EPSG:4326', crs, always_xy=True)
    inverse = Transformer.from_crs(crs, 'EPSG:4326', always_xy=True)
    samples = {str(s['sample_id']): s for s in manifest['samples']}
    result = []
    for region in regions:
        if 'longitude' in region and 'latitude' in region:
            lon, lat = float(region['longitude']), float(region['latitude'])
        elif 'world_x' in region and 'world_y' in region:
            lon, lat = inverse.transform(region['world_x'], region['world_y'])
        else:
            anchor = samples[region['anchor_sample_id']]
            x, y = forward.transform(anchor['longitude'], anchor['latitude'])
            lon, lat = inverse.transform(x + region['east_m'], y + region['north_m'])
        for field in ['tree_id', 'species', 'identification_source']:
            if field in region and (not isinstance(region[field], str) or len(region[field]) > 500):
                raise ValueError(f'Invalid crown {field}')
        result.append({**{k: region[k] for k in ['region_id','mode','radius_m','source','created_at','tree_id','species','identification_source'] if k in region},
                       'longitude': lon, 'latitude': lat})
    return result


def storage_payload(payload, manifest):
    if manifest['metadata'].get('curation_schema_version') != 2:
        return payload
    result = dict(payload)
    result['schema_version'] = 2
    result['tree_reviews'] = tree_records(result.pop('reviews'), manifest)
    result['mask_regions'] = geographic_regions(result.get('mask_regions', []), manifest)
    result = migrate_crown_storage(result)
    result['scope'] = {'review_id': manifest['metadata']['review_id'],
                       'imagery_id': manifest['metadata'].get('curation_imagery_id'),
                       'source_raster': manifest['metadata'].get('source_raster'),
                       'crs': manifest['metadata']['curation_crs']}
    return result


def migrate_crown_storage(payload):
    """Lossless/idempotent schema-2 upgrade; never replace a tree's center."""
    result = deepcopy(payload)
    records = result.setdefault('tree_reviews', {})
    added = result.setdefault('added_trees', {})
    regions = []
    for region in result.get('mask_regions', []):
        if region.get('mode') != 'confirmed-tree':
            regions.append(region)
            continue
        if region.get('tree_id'):
            record = records.setdefault(str(region['tree_id']), {})
            radius = record.get('crown_radius_m')
            if radius is not None and radius != region['radius_m']:
                raise ValueError(f"Conflicting crown radii for {region['tree_id']}")
            record['crown_radius_m'] = region['radius_m']
            record['crown_source'] = region.get('source', 'human')
        else:
            tid = 'manual-' + region['region_id']
            tree = {k: v for k, v in region.items() if k not in {'mode', 'radius_m'}}
            tree['crown_radius_m'] = region['radius_m']
            if tid in added and added[tid] != tree:
                raise ValueError(f'Conflicting added tree {tid}')
            added[tid] = tree
    result['mask_regions'] = regions
    result['crown_storage_version'] = 1
    return result
