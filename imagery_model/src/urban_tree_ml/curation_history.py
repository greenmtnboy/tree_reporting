"""Lossless, resumable compaction of legacy curation ZIPs (no live-state edits).

Each SQLite pack is standalone: revisions reference exact file bytes assembled
from shared compressed chunks. The catalog maps original revision IDs to packs.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from concurrent.futures import ProcessPoolExecutor
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import time
import zipfile
import zlib

if __package__:
    from .curation_archive import _chunks, _valid_sha, _write_json_atomic, canonical, digest, command
else:
    from curation_archive import _chunks, _valid_sha, _write_json_atomic, canonical, digest, command

ALLOWED = {'archive.json', 'bundle.json', 'reviews.json', 'manifest.json', 'training-feedback.json', 'chip-tags.json'}


@contextmanager
def _connect_readonly(path):
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    try:
        yield db
    finally:
        db.close()


def read_revision(pack, revision):
    if not _valid_sha(revision):
        raise ValueError('Invalid historical revision')
    with _connect_readonly(pack) as db:
        row = db.execute('SELECT files FROM revisions WHERE id=?', (revision,)).fetchone()
        if row is None:
            raise ValueError('Historical revision missing from pack')
        result = {}
        for name, sha in json.loads(row[0]).items():
            if name not in ALLOWED or not _valid_sha(sha):
                raise ValueError('Invalid historical file')
            size, parts = db.execute('SELECT size,parts FROM files WHERE sha=?', (sha,)).fetchone()
            content = []
            for part in json.loads(parts):
                value = zlib.decompress(db.execute('SELECT data FROM chunks WHERE sha=?', (part,)).fetchone()[0])
                if hashlib.sha256(value).hexdigest() != part:
                    raise ValueError('Historical chunk checksum mismatch')
                content.append(value)
            value = b''.join(content)
            if len(value) != size or hashlib.sha256(value).hexdigest() != sha:
                raise ValueError('Historical file checksum mismatch')
            result[name] = value
        return result


def verify_pack(path):
    """Read every persisted unique chunk/file; validate revision references."""
    with _connect_readonly(path) as db:
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Historical database integrity failure')
        for sha, data in db.execute('SELECT sha,data FROM chunks'):
            if hashlib.sha256(zlib.decompress(data)).hexdigest() != sha:
                raise ValueError('Historical chunk checksum mismatch')
        for sha, size, parts in db.execute('SELECT sha,size,parts FROM files'):
            actual = hashlib.sha256()
            length = 0
            for part in json.loads(parts):
                data = zlib.decompress(db.execute('SELECT data FROM chunks WHERE sha=?', (part,)).fetchone()[0])
                actual.update(data)
                length += len(data)
            if length != size or actual.hexdigest() != sha:
                raise ValueError('Historical file checksum mismatch')
        revisions = dict(db.execute('SELECT id,files FROM revisions'))
        known = {row[0] for row in db.execute('SELECT sha FROM files')}
        for revision, files in revisions.items():
            refs = json.loads(files)
            if not _valid_sha(revision) or not set(refs).issubset(ALLOWED) or not set(refs.values()).issubset(known):
                raise ValueError('Invalid historical revision references')
    return len(revisions)


def _build_batch(args):
    source, output, number, entries = args
    source, output = Path(source).resolve(), Path(output).resolve()
    receipt = output / 'batches' / f'{number:06d}.json'
    if receipt.exists():
        saved = json.loads(receipt.read_text())
        pack = output / 'packs' / (saved['pack'] + '.sqlite')
        if saved['inputs'] != entries or digest(pack) != saved['pack']:
            raise ValueError('Previously compacted batch changed')
        return saved
    temporary = output / 'packs' / f'.batch-{number:06d}.tmp'
    # Only our own incomplete batch file is replaced, never a published pack.
    temporary.unlink(missing_ok=True)
    db = sqlite3.connect(temporary)
    db.executescript('''
        PRAGMA journal_mode=OFF;
        PRAGMA synchronous=OFF;
        CREATE TABLE chunks(sha TEXT PRIMARY KEY, data BLOB NOT NULL);
        CREATE TABLE files(sha TEXT PRIMARY KEY, size INTEGER NOT NULL, parts TEXT NOT NULL);
        CREATE TABLE revisions(id TEXT PRIMARY KEY, files TEXT NOT NULL);
    ''')
    known_files, known_chunks = set(), set()
    originals = {}
    try:
        for item in entries:
            revision = item['revision']
            if not _valid_sha(revision):
                raise ValueError('Unsafe source revision')
            path = source / (revision + '.zip')
            stat = path.stat()
            if stat.st_size != item['bytes'] or stat.st_mtime_ns != item['mtime_ns']:
                raise ValueError('Legacy ZIP changed during compaction')
            packet = path.read_bytes()
            with zipfile.ZipFile(io.BytesIO(packet)) as archive:
                names = archive.namelist()
                if len(names) != len(set(names)) or not set(names).issubset(ALLOWED):
                    raise ValueError('Unexpected legacy ZIP entries')
                files = {name: archive.read(name) for name in names}
            identity = json.loads(files['archive.json'])
            if identity['revision'] != revision or hashlib.sha256(canonical({k:v for k,v in identity.items() if k != 'revision'})).hexdigest() != revision:
                raise ValueError('Legacy revision identity mismatch')
            bundle = json.loads(files['bundle.json'])
            for name, expected in bundle['files'].items():
                if hashlib.sha256(files[name]).hexdigest() != expected['sha256']:
                    raise ValueError('Legacy bundle checksum mismatch')
            references = {}
            for name, content in files.items():
                sha = hashlib.sha256(content).hexdigest()
                references[name] = sha
                if sha in known_files:
                    continue
                parts = []
                for part in _chunks(content):
                    part_sha = hashlib.sha256(part).hexdigest()
                    parts.append(part_sha)
                    if part_sha not in known_chunks:
                        db.execute('INSERT INTO chunks VALUES (?,?)', (part_sha, zlib.compress(part)))
                        known_chunks.add(part_sha)
                db.execute('INSERT INTO files VALUES (?,?,?)', (sha, len(content), json.dumps(parts)))
                known_files.add(sha)
            db.execute('INSERT INTO revisions VALUES (?,?)', (revision, json.dumps(references)))
            originals[revision] = {'zip_sha256': hashlib.sha256(packet).hexdigest(), 'zip_bytes': len(packet), 'city': identity['city']}
        db.commit()
    finally:
        db.close()
    with temporary.open('r+b') as stream:
        os.fsync(stream.fileno())
    count = verify_pack(temporary)
    if count != len(entries):
        raise ValueError('Historical revision count differs')
    sha = digest(temporary)
    target = output / 'packs' / (sha + '.sqlite')
    os.replace(temporary, target)
    result = {'pack': sha, 'bytes': target.stat().st_size, 'inputs': entries, 'revisions': originals}
    _write_json_atomic(receipt, result)
    return result


def build(directory, workers=4, batch_size=256, limit=None):
    directory = Path(directory).resolve()
    source, output = directory / 'revisions', directory / 'history-v1'
    output.mkdir(exist_ok=True)
    lock = output / 'build.lock'
    with lock.open('x') as stream:
        json.dump({'pid':os.getpid()}, stream)
    try:
        (output / 'packs').mkdir(exist_ok=True)
        (output / 'batches').mkdir(exist_ok=True)
        plan_path = output / 'plan.json'
        if plan_path.exists():
            plan = json.loads(plan_path.read_text())
        else:
            paths = sorted(source.glob('*.zip'), key=lambda p:(p.stat().st_mtime_ns,p.name))
            if limit is not None:
                paths = paths[:limit]
            entries = [{'revision':p.stem,'bytes':p.stat().st_size,'mtime_ns':p.stat().st_mtime_ns} for p in paths]
            plan = {'batch_size':batch_size, 'entries':entries}
            _write_json_atomic(plan_path, plan)
        entries, batch_size = plan['entries'], plan['batch_size']
        tasks = [(str(source),str(output),i//batch_size,entries[i:i+batch_size]) for i in range(0,len(entries),batch_size)]
        catalog = {'schema_version':1,'revisions':{},'packs':{}}
        started = time.monotonic()
        with ProcessPoolExecutor(max_workers=workers) as pool:
            for batch in pool.map(_build_batch, tasks):
                pack = batch['pack']
                catalog['packs'][pack] = {'bytes':batch['bytes'],'revisions':len(batch['revisions'])}
                for revision, meta in batch['revisions'].items():
                    catalog['revisions'][revision] = {**meta,'pack':pack}
                progress = {'completed':len(catalog['revisions']),'total':len(entries), 'source_bytes':sum(v['zip_bytes'] for v in catalog['revisions'].values()), 'packed_bytes':sum(v['bytes'] for v in catalog['packs'].values()), 'seconds':round(time.monotonic()-started)}
                _write_json_atomic(output/'progress.json',progress)
                print(json.dumps(progress),flush=True)
        _write_json_atomic(output/'catalog.json',catalog)
        return catalog
    finally:
        lock.unlink(missing_ok=True)


def publish(directory, bucket):
    """Publish immutable packs first; re-download every byte before retirement."""
    output = Path(directory).resolve() / 'history-v1'
    catalog_path = output / 'catalog.json'
    catalog = json.loads(catalog_path.read_text())
    gc = 'gcloud.cmd' if os.name == 'nt' else 'gcloud'
    for sha in catalog['packs']:
        if not _valid_sha(sha) or digest(output/'packs'/(sha+'.sqlite')) != sha:
            raise ValueError('Historical pack changed before publication')
    # Never silently replace an existing, different migration catalog.
    import subprocess
    try:
        existing = json.loads(command(gc,'storage','cat',f'{bucket}/history-v1/catalog.json'))
    except subprocess.CalledProcessError as error:
        if not any(t in (error.stderr or '').lower() for t in ('no urls matched','matched no objects or files','not found','404')):
            raise
    else:
        if existing != catalog:
            raise ValueError('Different remote history catalog already exists')
    print('Uploading historical packs',flush=True)
    command(gc,'storage','rsync',output/'packs',f'{bucket}/history-v1/packs','--recursive','--exclude=.*\\.tmp$')
    # Ship the compatible restore utilities alongside the existing annotation
    # backup tools. Only these two paths are staged; live annotations are not read.
    import shutil
    publisher=Path(directory).resolve()/'publisher'
    lock=Path(directory).resolve()/'backup.lock'
    with lock.open('x') as stream:
        json.dump({'pid':os.getpid(),'operation':'publish-history-restore-tools'},stream)
    try:
        if not (publisher/'.git').exists():
            raise ValueError('Expected existing annotation-backup publisher checkout')
        if command('git','-C',publisher,'status','--porcelain'):
            raise ValueError('Publisher has unrelated work; refusing to stage it')
        command('git','-C',publisher,'pull','--ff-only')
        for name in ['curation_archive.py','curation_history.py']:
            shutil.copy2(Path(__file__).with_name(name),publisher/'tools'/name)
        command('git','-C',publisher,'add','--','tools/curation_archive.py','tools/curation_history.py')
        if command('git','-C',publisher,'diff','--cached','--name-only'):
            command('git','-C',publisher,'commit','-m','Support lossless compact historical curation backups')
        command('git','-C',publisher,'push','origin','HEAD:main')
    finally:
        lock.unlink(missing_ok=True)
    command(gc,'storage','cp',catalog_path,f'{bucket}/history-v1/catalog.json')
    verification = output/'remote-verification'
    (verification/'packs').mkdir(parents=True,exist_ok=True)
    print('Independently downloading every historical pack for verification',flush=True)
    command(gc,'storage','rsync',f'{bucket}/history-v1/packs',verification/'packs','--recursive')
    command(gc,'storage','cp',f'{bucket}/history-v1/catalog.json',verification/'catalog.json')
    if digest(catalog_path) != digest(verification/'catalog.json'):
        raise ValueError('Remote historical catalog differs')
    for sha in catalog['packs']:
        if digest(verification/'packs'/(sha+'.sqlite')) != sha:
            raise ValueError('Downloaded historical pack checksum mismatch')
    receipt = {'catalog_sha256':digest(catalog_path),'bucket':bucket,'packs':len(catalog['packs']),'revisions':len(catalog['revisions']),'verified_at':time.time()}
    _write_json_atomic(output/'REMOTE_VERIFIED.json',receipt)
    print(json.dumps(receipt),flush=True)


def verify_restore_samples(directory, bucket):
    """Compare boundaries of every downloaded pack and full-restore each city."""
    from concurrent.futures import ThreadPoolExecutor
    if __package__:
        from .curation_archive import restore
    else:
        from curation_archive import restore
    directory=Path(directory).resolve()
    output=directory/'history-v1'
    catalog_path=output/'catalog.json'
    catalog=json.loads(catalog_path.read_text())
    receipt=json.loads((output/'REMOTE_VERIFIED.json').read_text())
    if receipt['catalog_sha256']!=digest(catalog_path) or receipt['bucket']!=bucket:
        raise ValueError('Remote verification does not match catalog')
    samples={}
    compared=0
    for path in sorted((output/'batches').glob('*.json')):
        batch=json.loads(path.read_text())
        pack=output/'remote-verification'/'packs'/(batch['pack']+'.sqlite')
        if digest(pack)!=batch['pack']:
            raise ValueError('Downloaded pack changed before restore check')
        for entry in [batch['inputs'][0],batch['inputs'][-1]]:
            revision=entry['revision']
            with zipfile.ZipFile(directory/'revisions'/(revision+'.zip')) as z:
                expected={name:z.read(name) for name in z.namelist()}
            if read_revision(pack,revision)!=expected:
                raise ValueError('Remote pack reconstruction differs from original ZIP')
            compared+=1
        for revision,record in batch['revisions'].items():
            samples.setdefault(record['city'],revision)
    print(f'Compared {compared} boundary revisions from downloaded packs byte-for-byte',flush=True)
    def full_sample(pair):
        city,revision=pair
        destination=output/'restore-samples'/city
        marker=destination/'RESTORE_VERIFIED.json'
        if marker.exists() and json.loads(marker.read_text())['revision']==revision:
            return {'city':city,**json.loads(marker.read_text())}
        restore(directory.parent,revision,destination,bucket)
        return {'city':city,**json.loads(marker.read_text())}
    with ThreadPoolExecutor(max_workers=2) as pool:
        restored=list(pool.map(full_sample,samples.items()))
    result={'catalog_sha256':digest(catalog_path),'boundary_revisions_compared':compared,'full_restores':restored}
    _write_json_atomic(output/'RESTORE_SAMPLES_VERIFIED.json',result)
    print(json.dumps(result),flush=True)


def _retirement_target(directory, revision, record):
    """Only a checksum-identical, individually named legacy ZIP can be removed."""
    source = (Path(directory).resolve()/'revisions').resolve()
    if not _valid_sha(revision):
        raise ValueError('Unsafe retirement revision')
    target = (source/(revision+'.zip')).resolve()
    if target.parent != source:
        raise ValueError('Legacy source escapes archive directory')
    if target.exists() and (target.stat().st_size != record['zip_bytes'] or digest(target) != record['zip_sha256']):
        raise ValueError('Original ZIP changed; refusing retirement')
    return target


def retire(directory, bucket):
    """Replace verified original ZIPs; retain packs, catalog and an audit trail.

    Remote deletes are exact-name and generation-conditioned. GCS soft deletion
    retains the originals according to the bucket policy; no purge is attempted.
    """
    import base64
    from concurrent.futures import ThreadPoolExecutor
    import requests
    from urllib.parse import quote

    directory = Path(directory).resolve()
    output = directory/'history-v1'
    catalog_path = output/'catalog.json'
    catalog = json.loads(catalog_path.read_text())
    receipt = json.loads((output/'REMOTE_VERIFIED.json').read_text())
    restored = json.loads((output/'RESTORE_SAMPLES_VERIFIED.json').read_text())
    if receipt['catalog_sha256'] != digest(catalog_path) or receipt['bucket'] != bucket or restored['catalog_sha256'] != receipt['catalog_sha256']:
        raise ValueError('Cloud verification/restore evidence missing or stale')
    for sha in catalog['packs']:
        if digest(output/'packs'/(sha+'.sqlite')) != sha:
            raise ValueError('Local replacement pack changed')
    if not bucket.startswith('gs://') or '/' in bucket[5:]:
        raise ValueError('Expected an exact bucket root')
    bucket_name = bucket[5:]
    token = command('gcloud.cmd' if os.name == 'nt' else 'gcloud','auth','print-access-token')
    headers = {'Authorization':'Bearer '+token}
    base = 'https://storage.googleapis.com/storage/v1/b/'+quote(bucket_name,safe='')+'/o'
    # Re-check the remote catalog immediately before any destructive operation.
    response=requests.get(base+'/'+quote('history-v1/catalog.json',safe=''),headers=headers,params={'alt':'media'},timeout=60)
    response.raise_for_status()
    if hashlib.sha256(response.content).hexdigest()!=receipt['catalog_sha256']:
        raise ValueError('Published catalog changed; refusing retirement')
    remote = {}
    page = None
    while True:
        params={'prefix':'revisions/','fields':'nextPageToken,items(name,generation,size,md5Hash)','maxResults':1000}
        if page: params['pageToken']=page
        response=requests.get(base,headers=headers,params=params,timeout=60)
        response.raise_for_status()
        data=response.json()
        for item in data.get('items',[]):
            name=item['name']
            revision=name.removeprefix('revisions/').removesuffix('.zip')
            if name=='revisions/'+revision+'.zip' and revision in catalog['revisions']:
                remote[revision]=item
        page=data.get('nextPageToken')
        if not page: break
    print(f'Preflight: {len(catalog["revisions"])} local revisions, {len(remote)} matching cloud ZIPs',flush=True)
    def check(pair):
        revision,record=pair
        target=_retirement_target(directory,revision,record)
        item=remote.get(revision)
        if item:
            if not target.exists():
                raise ValueError('Cannot compare remote original without local ZIP')
            with target.open('rb') as stream:
                md5=base64.b64encode(hashlib.file_digest(stream,'md5').digest()).decode()
            if md5 != item.get('md5Hash') or target.stat().st_size != int(item['size']):
                raise ValueError('Cloud original differs from verified local ZIP')
        return revision
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(check,catalog['revisions'].items()))
    _write_json_atomic(output/'retirement-plan.json',{'catalog_sha256':receipt['catalog_sha256'],'remote':remote})
    from threading import local
    connections=local()
    def remove(pair):
        revision,record=pair
        target=_retirement_target(directory,revision,record)
        item=remote.get(revision)
        if item:
            # Reuse one TLS connection per worker instead of establishing tens
            # of thousands of connections. Generation guards make retries safe.
            if not hasattr(connections,'session'):
                from requests.adapters import HTTPAdapter
                from urllib3.util.retry import Retry
                connections.session=requests.Session()
                connections.session.headers.update(headers)
                connections.session.mount('https://',HTTPAdapter(max_retries=Retry(total=4,backoff_factor=.5,status_forcelist=[429,500,502,503,504],allowed_methods=['DELETE'])))
            response=connections.session.delete(base+'/'+quote(item['name'],safe=''),params={'ifGenerationMatch':item['generation']},timeout=60)
            # 404 permits resumption after a verified deletion; 412 fails closed.
            if response.status_code!=404: response.raise_for_status()
        existed=target.exists()
        target.unlink(missing_ok=True)
        return {'revision':revision,'local_removed':existed,'remote_generation_removed':item['generation'] if item else None,'bytes':record['zip_bytes']}
    results=[]
    with (output/'retirement-audit.jsonl').open('a',encoding='utf-8') as audit:
        with ThreadPoolExecutor(max_workers=16) as pool:
            for result in pool.map(remove,catalog['revisions'].items()):
                audit.write(json.dumps(result)+'\n');audit.flush()
                results.append(result)
                if len(results)%1000==0: print('Retired '+str(len(results))+' verified originals',flush=True)
        os.fsync(audit.fileno())
    result={'revisions':len(results),'local_bytes_removed':sum(r['bytes'] for r in results if r['local_removed']),'remote_bytes_retired':sum(r['bytes'] for r in results if r['remote_generation_removed']),'remote_objects_retired':sum(bool(r['remote_generation_removed']) for r in results),'catalog_sha256':receipt['catalog_sha256']}
    _write_json_atomic(output/'RETIREMENT_COMPLETE.json',result)
    print(json.dumps(result),flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',type=Path,required=True)
    parser.add_argument('--workers',type=int,default=4)
    parser.add_argument('--batch-size',type=int,default=256)
    parser.add_argument('--publish',action='store_true')
    parser.add_argument('--retire',action='store_true')
    parser.add_argument('--verify-restores',action='store_true')
    parser.add_argument('--bucket',default='gs://arborary-world-curation-archive')
    args = parser.parse_args()
    if args.publish:
        publish(args.directory,args.bucket)
    elif args.retire:
        retire(args.directory,args.bucket)
    elif args.verify_restores:
        verify_restore_samples(args.directory,args.bucket)
    else:
        build(args.directory,args.workers,args.batch_size)
