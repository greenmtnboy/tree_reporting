#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["duckdb"]
# ///
"""Publish an atomic species release: index, catalogue and individual records.

The index is the only mutable pointer. Upload the entire immutable release
first, then compare-and-swap index.json. Failed or overlapping runs cannot
expose a partial release. No website checkout or deployment is involved.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.error import HTTPError

from publish_homepage import (
    BUCKET, PUBLIC_HOST, HmacClient, PublishError, city_url, connect, dumps,
    ecoregion_url, enrichment_url, load_cities, pinned, read_json, snapshot_inputs,
)
from species_catalogue import collect, to_index

ROOT = "arborary/website/v1/species"
INDEX = f"{ROOT}/index.json"
SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def read_live(client):
    url = f"{PUBLIC_HOST}/{BUCKET}/{INDEX}"
    if client:
        generation = client.generations(BUCKET, INDEX).get(INDEX)
        return (read_json(pinned(url, generation)), generation) if generation else (None, 0)
    try:
        return read_json(url), 0
    except HTTPError as exc:
        if exc.code == 404:
            return None, 0
        raise


def validate(catalogue: dict) -> None:
    rows = catalogue["species"]
    if not rows or len(rows) != min(catalogue["top_n"], catalogue["species_count_total"]):
        raise PublishError("incomplete species catalogue")
    if len({s['slug'] for s in rows}) != len(rows) or len({s['species'] for s in rows}) != len(rows):
        raise PublishError("duplicate species or slug")
    if sum(s['trees'] for s in rows) != catalogue['named_trees_in_catalogue']:
        raise PublishError("catalogue count disagrees")
    if not 0 < catalogue['named_trees_in_catalogue'] <= catalogue['named_trees_total']:
        raise PublishError("invalid named tree totals")
    if sum(c['named_trees'] for c in catalogue['cities']) != catalogue['named_trees_total']:
        raise PublishError("city totals disagree")
    registry = catalogue['slug_registry']
    if len(set(registry.values())) != len(registry) or not all(SLUG.fullmatch(s) for s in registry.values()):
        raise PublishError("invalid slug registry")
    for rank, s in enumerate(rows, 1):
        if (not SLUG.fullmatch(s['slug']) or registry.get(s['species']) != s['slug']
                or s['rank'] != rank or s['trees'] <= 0
                or not 0 < s['genus_rank'] <= catalogue['genus_count_total']
                or s['genus_trees'] < s['trees']
                or s['city_count'] != len(s['cities'])
                or sum(c['trees'] for c in s['cities']) != s['trees']):
            raise PublishError(f"invalid species record: {s['species']}")


def build(cities, generations, live):
    previous = {c['code'] for c in (live or {}).get('cities', []) if c['named_trees'] > 0}
    published = {c['code']: generations[c['code']] for c in cities if generations[c['code']] is not None}
    if not published or previous - published.keys():
        raise PublishError("a previously published city is missing")
    con = connect()
    urls = ", ".join(f"'{pinned(city_url(code), gen)}'" for code, gen in published.items())
    con.execute(f"CREATE VIEW trees AS SELECT city, species, diameter_at_breast_height, plant_date "
                f"FROM read_parquet([{urls}], union_by_name=true)")
    registry = dict((live or {}).get('slug_registry') or json.loads(
        Path(__file__).with_name('species_slugs.json').read_text(encoding='utf-8')))
    try:
        catalogue = collect(con, cities, pinned(enrichment_url(), generations['enrichment']),
                            pinned(ecoregion_url(), generations['ecoregions']), registry)
    finally:
        con.close()
    current = {c['code'] for c in catalogue['cities'] if c['named_trees'] > 0}
    if previous - current:
        raise PublishError("a previously populated city has no named trees")
    catalogue.update(schema_version=1, slug_registry=registry)
    validate(catalogue)
    if live and catalogue['named_trees_total'] < live['named_trees_total'] * .75:
        raise PublishError('candidate loses more than a quarter of the named inventory')
    return catalogue


def release(catalogue):
    # The digest covers the complete catalogue including its generation time.
    # Catalogue bytes below are checked by the website's build-time consumer.
    body = dumps(catalogue)
    revision = hashlib.sha256(body).hexdigest()
    index = to_index({**catalogue, 'revision': revision})
    base = f"{ROOT}/releases/{revision}"
    objects = {f"{base}/catalogue.json": body}
    for species in catalogue['species']:
        objects[f"{base}/{species['slug']}.json"] = dumps({
            'schema_version': 1, 'revision': revision,
            'generated_at': catalogue['generated_at'], 'species': species,
        })
    return index, objects


def publish(client, index, objects, generation):
    # Bounded uploads. An exception prevents updating the index; older releases
    # remain available to cached indexes and open tabs. No automatic deletion.
    existing = client.generations(BUCKET, f"{ROOT}/releases/{index['revision']}/")
    def upload(item):
        name, body = item
        if name not in existing:
            client.put(BUCKET, name, body, if_generation_match=0)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(upload, objects.items()))
    return client.put(BUCKET, INDEX, dumps(index), if_generation_match=generation)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--output', type=Path, help='write the validated catalogue locally')
    args = parser.parse_args(argv)
    client = HmacClient.from_env()
    if not args.dry_run and client is None:
        raise PublishError('GOOGLE_HMAC_KEY / GOOGLE_HMAC_SECRET are not set')
    live, generation = read_live(client)
    cities = load_cities()
    catalogue = build(cities, snapshot_inputs(cities, client), live)
    if args.output:
        args.output.write_bytes(dumps(catalogue))
    index, objects = release(catalogue)
    if not args.dry_run:
        publish(client, index, objects, generation)
    print(f"[species] {'validated' if args.dry_run else 'published'} {len(index['species'])} entries")
    if not args.dry_run:
        print(f"::trilogy-output name=species_feed kind=link value={PUBLIC_HOST}/{BUCKET}/{INDEX}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
