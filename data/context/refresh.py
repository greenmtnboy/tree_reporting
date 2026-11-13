"""Build and optionally publish current city heat/water assets at stable paths."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import tempfile

import pyarrow.parquet as pq

from config import BUCKET, CITIES, PREFIX, PUBLIC, filenames, latest_summer
from sources import build_heat, build_water

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'website'))
from publish_homepage import HmacClient, PublishError, read_json, pinned


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def validate(output, city, metadata):
    heat_name, water_name, _ = filenames(city)
    if metadata.get('schema_version') != 1 or metadata.get('city') != city:
        raise ValueError('Context metadata schema or city mismatch')
    if set(metadata['assets']) != {heat_name, water_name}:
        raise ValueError('Context publication must contain only the heat and water assets')
    import duckdb
    with duckdb.connect() as con:
        for name, key, section in ((heat_name, 'observation_id', 'heat'), (water_name, 'feature_id', 'water')):
            total, distinct, other = con.execute(
                f'SELECT count(*), count(DISTINCT {key}), count(*) FILTER(WHERE city != ? OR city IS NULL) FROM read_parquet(?)',
                [city, str(output/name)]).fetchone()
            if not total or total != distinct or other or total != metadata[section]['rows']:
                raise ValueError(f'Invalid {section} row counts, keys or city')
        bad = con.execute('''SELECT count(*) FROM read_parquet(?) WHERE
            surface_temperature_c IS NULL OR NOT isfinite(surface_temperature_c)
            OR surface_temperature_c NOT BETWEEN -100 AND 100 OR valid_scene_count < 1
            OR latitude NOT BETWEEN -90 AND 90 OR longitude NOT BETWEEN -180 AND 180
            OR year(acquired_on) != ? OR month(acquired_on) NOT BETWEEN 6 AND 8''', [str(output/heat_name), metadata['heat']['year']]).fetchone()[0]
        if bad:
            raise ValueError('Invalid heat values or acquisition years')
    if b'geo' not in pq.read_schema(output/water_name).metadata:
        raise ValueError('Water asset lacks GeoParquet metadata')


def publish(output, city, metadata, client, generations, previous=None):
    heat_name, water_name, meta_name = filenames(city)
    validate(output, city, metadata)
    if previous:
        if metadata['heat']['year'] < previous['heat']['year']:
            raise ValueError('Refusing to replace current heat with an older summer')
        if metadata['generated_at'] <= previous['generated_at']:
            raise ValueError('A newer context build is already published')
        # Same-season/regional source losses need investigation, not silent replacement.
        if metadata['heat']['year'] == previous['heat']['year']:
            if metadata['heat']['rows'] < .75*previous['heat']['rows']:
                raise ValueError('Same-season heat coverage fell by more than 25%')
        if metadata['water']['rows'] < .75*previous['water']['rows']:
            raise ValueError('Water features fell by more than 25%')
    for name in (heat_name, water_name):
        path = output/name
        expected = metadata['assets'][name]
        if digest(path) != expected['sha256']:
            raise ValueError(f'Asset changed after validation: {name}')
        key = f'{PREFIX}/{name}'
        generation = client.put(BUCKET, key, path.read_bytes(),
            if_generation_match=generations.get(key, 0), content_type='application/vnd.apache.parquet',
            cache_control='public, max-age=300')
        expected['generation'] = generation
    body = (json.dumps(metadata, indent=2, allow_nan=False)+'\n').encode()
    (output/meta_name).write_bytes(body)
    key = f'{PREFIX}/{meta_name}'
    client.put(BUCKET, key, body, if_generation_match=generations.get(key, 0),
               content_type='application/json', cache_control='public, max-age=60')
    return f'{PUBLIC}/{meta_name}'


def main(default_city=None, default_publish=False):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--city', choices=sorted(CITIES), default=default_city or 'USBOS')
    p.add_argument('--year', type=int, default=latest_summer(), help='Latest completed June-August season by default; no silent fallback')
    p.add_argument('--output', type=Path)
    p.add_argument('--publish', action='store_true', default=default_publish)
    args = p.parse_args()
    if not args.publish and args.output is None:
        p.error('Local builds require --output to retain the assets')
    client = HmacClient.from_env() if args.publish else None
    if args.publish and client is None:
        raise PublishError('GOOGLE_HMAC_KEY / GOOGLE_HMAC_SECRET are required for upload')
    if not 2013 <= args.year <= latest_summer():
        raise ValueError('Choose a completed summer in the Landsat 8/9 period')
    city, config = args.city, CITIES[args.city]
    heat_name, water_name, meta_name = filenames(city)
    # Capture generations before the slow build so overlapping jobs cannot
    # overwrite a newer result. Each asset upload is atomic; metadata is last.
    generations = client.generations(BUCKET, f'{PREFIX}/{city.lower()}_') if client else {}
    gen = generations.get(f'{PREFIX}/{meta_name}')
    previous = read_json(pinned(f'{PUBLIC}/{meta_name}', gen)) if gen else None
    with tempfile.TemporaryDirectory(prefix='arborary-context-') as scratch:
        output = args.output or Path(scratch)/'output'
        output.mkdir(parents=True, exist_ok=True)
        if any((output/n).exists() for n in (heat_name, water_name, meta_name)):
            raise ValueError('Use a new local output directory')
        generated = datetime.now(timezone.utc).isoformat()
        water = build_water(city, config, output/water_name)
        heat = build_heat(city, config, args.year, output/heat_name, Path(scratch)/'scenes')
        metadata = {'schema_version': 1, 'city': city, 'generated_at': generated,
            'bbox_wgs84': config['bbox'], 'heat': heat, 'water': water,
            'assets': {name: {'url': f'{PUBLIC}/{name}', 'sha256': digest(output/name),
                              'bytes': (output/name).stat().st_size} for name in (heat_name, water_name)}}
        validate(output, city, metadata)
        (output/meta_name).write_text(json.dumps(metadata, indent=2, allow_nan=False)+'\n', encoding='utf-8')
        if client:
            print(f'::trilogy-output name=context_{city.lower()} kind=link value={publish(output, city, metadata, client, generations, previous)}')
        else:
            print(f'Validated locally, not uploaded: {output}')
        print(json.dumps({'city': city, 'year': args.year, 'heat_rows': heat['rows'],
                          'dates': heat['dates'], 'coverage_by_date': heat['coverage_by_date'], 'water_features': water['rows']}))
