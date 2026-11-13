#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["duckdb", "numpy", "pyarrow", "rasterio", "pyproj", "requests", "shapely>=2.1", "google-cloud-storage"]
# ///
"""Publish an already-built current city seed, without repeating the source fetch."""
import argparse
import json
from pathlib import Path
import subprocess
import shutil

from config import BUCKET, CITIES, PREFIX, PUBLIC, filenames
from refresh import HmacClient, PublishError, pinned, publish, read_json


class GcloudClient:
    """Local seed transport using the active gcloud login; cloud jobs use HMAC."""
    def __init__(self):
        from google.cloud import storage
        from google.oauth2.credentials import Credentials
        executable = shutil.which('gcloud.cmd') or shutil.which('gcloud')
        if not executable:
            raise ValueError('gcloud is not installed')
        token = subprocess.check_output([executable, 'auth', 'print-access-token'], text=True).strip()
        self.client = storage.Client(project='arborary-context', credentials=Credentials(token))

    def generations(self, bucket, prefix):
        return {b.name: int(b.generation) for b in self.client.list_blobs(bucket, prefix=prefix)}

    def put(self, bucket, name, body, *, if_generation_match, content_type, cache_control):
        blob = self.client.bucket(bucket).blob(name)
        blob.cache_control = cache_control
        blob.upload_from_string(body, content_type=content_type, if_generation_match=if_generation_match, timeout=180)
        return int(blob.generation)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('directory', type=Path)
    p.add_argument('--city', choices=sorted(CITIES), default='USBOS')
    p.add_argument('--gcloud', action='store_true', help='Use the active local gcloud login instead of cloud HMAC credentials')
    args = p.parse_args()
    client = GcloudClient() if args.gcloud else HmacClient.from_env()
    if client is None:
        raise PublishError('GOOGLE_HMAC_KEY / GOOGLE_HMAC_SECRET are required')
    metadata_name = filenames(args.city)[2]
    metadata = json.loads((args.directory/metadata_name).read_text(encoding='utf-8'))
    generations = client.generations(BUCKET, f'{PREFIX}/{args.city.lower()}_')
    generation = generations.get(f'{PREFIX}/{metadata_name}')
    previous = read_json(pinned(f'{PUBLIC}/{metadata_name}', generation)) if generation else None
    print(publish(args.directory, args.city, metadata, client, generations, previous))


if __name__ == '__main__':
    main()
