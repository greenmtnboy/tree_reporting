#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["duckdb"]
# ///
"""Publish a verified context bundle, with its immutable manifest uploaded last."""
import argparse
import hashlib
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from context_release import digest, verify
from publish_homepage import BUCKET, PUBLIC_HOST, HmacClient, PublishError

ROOT = 'arborary/context/v1'
TYPES = {'.json': 'application/json', '.parquet': 'application/vnd.apache.parquet',
         '.tif': 'image/tiff', '.geojson': 'application/geo+json', '.npz': 'application/octet-stream'}


def publish(bundle, client=None):
    """No mutable latest pointer: callers link the exact revision they used."""
    bundle = Path(bundle)
    manifest = verify(bundle)
    study = manifest['study']
    if not re.fullmatch('[a-z0-9]+(?:-[a-z0-9]+)*', study):
        raise ValueError('Invalid study slug')
    revision = digest(bundle / 'manifest.json')
    prefix = f'{ROOT}/{study}/releases/{revision}'
    url = f'{PUBLIC_HOST}/{BUCKET}/{prefix}/manifest.json'
    if client is not None:
        # Never overwrite immutable objects. A failed run exposes no manifest;
        # retry skips completed objects and finishes the same release.
        existing = client.generations(BUCKET, prefix + '/')
        for name in [*manifest['assets'], 'manifest.json']:
            key = f'{prefix}/{name}'
            if key not in existing:
                path = bundle / name
                body = path.read_bytes()
                expected = revision if name == 'manifest.json' else manifest['assets'][name]['sha256']
                if hashlib.sha256(body).hexdigest() != expected:
                    raise ValueError(f'Bundle changed during publication: {name}')
                client.put(BUCKET, key, body, if_generation_match=0,
                           content_type=TYPES.get(path.suffix, 'application/octet-stream'),
                           cache_control='public, max-age=31536000, immutable')
    return url


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('bundle', type=Path)
    p.add_argument('--publish', action='store_true', help='Upload; default only validates and prints the proposed URL')
    args = p.parse_args()
    client = HmacClient.from_env() if args.publish else None
    if args.publish and client is None:
        raise PublishError('GOOGLE_HMAC_KEY / GOOGLE_HMAC_SECRET are not set')
    url = publish(args.bundle, client)
    print(('Published: ' if args.publish else 'Validated; NOT uploaded: ') + url)


if __name__ == '__main__':
    main()
