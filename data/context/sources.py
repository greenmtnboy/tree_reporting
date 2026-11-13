"""Current source adapters; source observations only, no trees or study aggregates."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
import json
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from pyproj import CRS, Transformer
import rasterio
from rasterio.transform import from_origin
from rasterio.vrt import WarpedVRT
from rasterio.warp import transform_bounds
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import shapely
from shapely.geometry import shape

STAC = 'https://planetarycomputer.microsoft.com/api/stac/v1/search'
QUALITY = ('Exclude fill/cloud/cirrus/shadow/snow/water and medium/high cloud confidence; '
           'ST uncertainty <=3 K; distance from cloud >=1 km. Same-date valid scenes averaged.')


def get_json(url, **params):
    with requests.Session() as session:
        session.headers['User-Agent'] = 'arborary.world city environmental context'
        session.mount('https://', HTTPAdapter(max_retries=Retry(total=4, backoff_factor=1,
                      status_forcelist=[429, 500, 502, 503, 504])))
        r = session.get(url, params=params, timeout=(20, 180))
        r.raise_for_status()
        result = r.json()
    if 'error' in result:
        raise ValueError(f'Source error: {result["error"]}')
    return result


def clear_land_mask(dn, qa, uncertainty, distance):
    return ((dn > 0) & ((qa & 191) == 0) & (((qa >> 8) & 3) < 2)
            & (uncertainty > 0) & (uncertainty <= 300) & (distance >= 100))


def catalog(config, year):
    result = get_json(STAC, collections=config['heat_collection'],
                      bbox=','.join(map(str, config['bbox'])),
                      datetime=f'{year}-06-01T00:00:00Z/{year}-08-31T23:59:59Z', limit=100)
    items = result['features']
    while links := [x for x in result.get('links', []) if x['rel'] == 'next']:
        result = get_json(links[0]['href'])
        items.extend(result['features'])
    selected = {x['id']: x for x in items if x['id'].startswith(('LC08', 'LC09'))
                and x['id'].endswith('T1') and all(b in x['assets'] for b in ('lwir11', 'qa_pixel', 'qa', 'cdist'))}
    if not selected:
        raise ValueError(f'No usable Landsat 8/9 Tier 1 source scenes for summer {year}; no silent year fallback')
    return [selected[k] for k in sorted(selected)]


def mosaic(arrays):
    stack = np.stack(arrays)
    n = np.isfinite(stack).sum(axis=0).astype('uint16')
    mean = np.divide(np.nansum(stack, axis=0), n,
                     out=np.full(n.shape, np.nan, dtype='float32'), where=n > 0)
    return mean, n


def heat_table(city, day, mean, counts, transform, crs):
    rows, cols = np.where(np.isfinite(mean))
    east, north = transform * (cols + .5, rows + .5)
    lon, lat = Transformer.from_crs(crs, 4326, always_xy=True).transform(east, north)
    cells = [f'{crs}:30:{int(e)}:{int(n)}' for e, n in zip(np.floor(east/30), np.floor(north/30))]
    return pa.table({
        'observation_id': pa.array([f'{city}:{day}:{cell}' for cell in cells], type=pa.string()),
        'cell_id': pa.array(cells, type=pa.string()), 'city': pa.array([city]*len(rows), type=pa.string()),
        'acquired_on': pa.array([date.fromisoformat(day)]*len(rows), type=pa.date32()),
        'easting_m': east, 'northing_m': north, 'longitude': lon, 'latitude': lat,
        'surface_temperature_c': mean[rows, cols], 'valid_scene_count': counts[rows, cols],
        'source': pa.array(['LANDSAT_C2_L2']*len(rows), type=pa.string()),
    })


def build_heat(city, config, year, output, cache):
    items = catalog(config, year)  # Always query the current catalog.
    cache = Path(cache)
    cache.mkdir(parents=True, exist_ok=True)
    crs = config['crs']
    left, bottom, right, top = transform_bounds('EPSG:4326', crs, *config['bbox'])
    left, bottom = np.floor(np.array([left, bottom])/30)*30
    right, top = np.ceil(np.array([right, top])/30)*30
    transform = from_origin(left, top, 30, 30)
    width, height = int((right-left)/30), int((top-bottom)/30)
    def scene(item):
        # Cache is scratch space for this build, never a previous season's input.
        arrays = {}
        for band in ('lwir11', 'qa_pixel', 'qa', 'cdist'):
            href = get_json('https://planetarycomputer.microsoft.com/api/sas/v1/sign',
                            href=item['assets'][band]['href'])['href']
            with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN='EMPTY_DIR', GDAL_HTTP_MAX_RETRY='3',
                              GDAL_HTTP_RETRY_DELAY='2', GDAL_HTTP_TIMEOUT='120'):
                with rasterio.open(href) as src, WarpedVRT(src, crs=crs, transform=transform,
                        width=width, height=height, nodata=0) as vrt:
                    arrays[band] = vrt.read(1)
        valid = clear_land_mask(arrays['lwir11'], arrays['qa_pixel'], arrays['qa'], arrays['cdist'])
        result = np.where(valid, arrays['lwir11']*.00341802+149-273.15, np.nan).astype('float32')
        path = cache / (item['id'] + '.npy')
        np.save(path, result)
        print(f"Heat {item['id']}: {valid.mean():.1%} usable", flush=True)
        return path
    with ThreadPoolExecutor(max_workers=3) as pool:
        paths = list(pool.map(scene, items))
    dates = sorted({x['properties']['datetime'][:10] for x in items})
    coverage, writer, total = {}, None, 0
    try:
        for day in dates:
            mean, counts = mosaic([np.load(p) for p, x in zip(paths, items)
                                    if x['properties']['datetime'][:10] == day])
            table = heat_table(city, day, mean, counts, transform, crs)
            coverage[day] = {'valid_pixels': table.num_rows, 'grid_fraction': table.num_rows/(width*height)}
            if writer is None:
                writer = pq.ParquetWriter(output, table.schema, compression='zstd')
            writer.write_table(table)
            total += table.num_rows
    finally:
        if writer:
            writer.close()
    if total == 0:
        raise ValueError('No valid heat observations; refusing empty publication')
    return {'provider': 'USGS Landsat 8/9 Collection 2 Level-2', 'collection': config['heat_collection'],
            'source_url': 'https://www.usgs.gov/landsat-missions/landsat-collection-2-surface-temperature',
            'access_url': 'https://planetarycomputer.microsoft.com/dataset/landsat-c2-l2',
            'year': year, 'season': 'June-August', 'dates': dates, 'coverage_by_date': coverage,
            'crs': crs, 'transform': list(transform)[:6], 'width': width, 'height': height,
            'delivered_pixel_m': 30, 'native_thermal_sampling_m': 100, 'units': 'degree Celsius',
            'resampling': 'nearest', 'processing_version': 'landsat-clear-land-v1',
            'quality': QUALITY, 'rows': total, 'rights': 'USGS Landsat data: public domain; credit USGS.',
            'missingness': 'Only valid pixel/date observations are rows. Missing rows are unknown, never zero. Coverage is fraction of the rectangular grid, not land area.',
            'interpretation': 'Clear-sky late-morning surface temperature, not air temperature or an all-weather summer average. No cross-date mean or common-pixel filter applied.',
            'items': [{'id': x['id'], 'datetime': x['properties']['datetime'],
                       'temperature_url': x['assets']['lwir11']['href']} for x in items]}


def write_water(path, city, features):
    geometries = [shapely.make_valid(shape(f['geometry'])) for f in features]
    if not geometries or any(g.is_empty or g.geom_type not in ('Polygon', 'MultiPolygon') for g in geometries):
        raise ValueError('Empty or non-polygon water source')
    ids = [f"{city}:MASSGIS:{f['properties']['OBJECTID']}" for f in features]
    if len(set(ids)) != len(ids):
        raise ValueError('Duplicate source water feature IDs')
    table = pa.table({'feature_id': ids, 'city': [city]*len(ids),
        'source': ['MASSGIS_MASSDEP']*len(ids),
        'source_feature_id': [str(f['properties']['OBJECTID']) for f in features],
        'name': pa.array([f['properties'].get('NAME') for f in features], type=pa.string()),
        'water_class': [str(f['properties']['POLY_CODE']) for f in features],
        'geometry_wkt': [g.wkt for g in geometries],
        'geometry': pa.array([shapely.to_wkb(g) for g in geometries], type=pa.binary())})
    bounds = shapely.total_bounds(geometries).tolist()
    geo = {'version': '1.1.0', 'primary_column': 'geometry', 'columns': {'geometry': {
        'encoding': 'WKB', 'geometry_types': sorted({g.geom_type for g in geometries}),
        'crs': CRS.from_epsg(4326).to_json_dict(), 'bbox': bounds}}}
    pq.write_table(table.replace_schema_metadata({b'geo': json.dumps(geo).encode()}), path, compression='zstd')
    return len(ids)


def build_water(city, config, output):
    service = config['water_service']
    source_meta = get_json(service, f='json')
    ids = get_json(service+'/query', f='json', where=config['water_where'],
                   geometry=','.join(map(str, config['bbox'])), geometryType='esriGeometryEnvelope',
                   inSR=4326, spatialRel='esriSpatialRelIntersects', returnIdsOnly='true')
    object_ids = sorted(ids.get('objectIds') or [])
    if not object_ids:
        raise ValueError('No water features returned; refusing empty publication')
    features = []
    for start in range(0, len(object_ids), 100):
        page = get_json(service+'/query', f='geojson',
            objectIds=','.join(map(str, object_ids[start:start+100])), outFields='OBJECTID,NAME,POLY_CODE',
            outSR=4326, returnGeometry='true')
        if page.get('exceededTransferLimit'):
            raise ValueError('Truncated water response')
        features.extend(page['features'])
    if {f['properties']['OBJECTID'] for f in features} != set(object_ids) or len(features) != len(object_ids):
        raise ValueError('Incomplete water response')
    count = write_water(output, city, features)
    return {'provider': config['water_credit'], 'source_url': config['water_source_url'],
            'service': service, 'source_modified_epoch_ms': source_meta.get('editingInfo', {}).get('lastEditDate'),
            'filter': config['water_where'], 'rows': count, 'crs': 'EPSG:4326',
            'geometry_policy': 'Full intersecting source polygons, no simplification; invalid geometries repaired. Clip to study bounds when calculating area.',
            'rights': 'Retains MassGIS / USGS / MassDEP source attribution and terms; no blanket relicensing.'}
