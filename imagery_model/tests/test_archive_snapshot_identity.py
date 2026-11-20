import hashlib
import json

import pytest

from urban_tree_ml import curation_archive as archive


def test_acquisition_registers_nested_vrt_source_bytes(tmp_path):
    leaf=tmp_path/'source.tif';leaf.write_bytes(b'actual imagery')
    nested=tmp_path/'nested.vrt';nested.write_text('<VRTDataset><GDALWarpOptions><SourceDataset relativeToVRT="1">source.tif</SourceDataset></GDALWarpOptions></VRTDataset>')
    top=tmp_path/'imagery.vrt';top.write_text('<VRTDataset><SourceFilename relativeToVRT="1">nested.vrt</SourceFilename><SourceFilename relativeToVRT="1">nested.vrt</SourceFilename></VRTDataset>')
    record=archive.acquisition(tmp_path,top,register=True)
    assert {a['name'] for a in record['assets']}=={'source.tif','nested.vrt','imagery.vrt'}
    assert next(a['sha256'] for a in record['assets'] if a['name']=='source.tif')==archive.digest(leaf)
    leaf.write_bytes(b'changed source pixels')
    with pytest.raises(ValueError,match='Registered imagery changed'):archive.acquisition(tmp_path,top)


def test_regenerated_timestamps_create_distinct_immutable_snapshots(tmp_path, monkeypatch):
    bundle_dir = tmp_path / 'bundle'
    review_dir = tmp_path / 'review'
    bundle_dir.mkdir()
    review_dir.mkdir()
    (review_dir / 'scene.png').write_bytes(b'fixture image')
    monkeypatch.setattr(archive, 'acquisition', lambda *args: {'acquisition_id': 'a' * 64})
    def write_bundle(timestamp):
        files = {'manifest.json': {'scenes': [{'image': 'scene.png'}]},
                 'reviews.json': {'saved_at': timestamp, 'scene_reviews': {}},
                 'training-feedback.json': {'created_at': timestamp, 'trees': []}}
        records = {}
        for name, value in files.items():
            content = json.dumps(value).encode()
            (bundle_dir / name).write_bytes(content)
            records[name] = {'sha256': hashlib.sha256(content).hexdigest()}
        bundle = {'city': 'ussfo', 'review_id': 'fixture', 'files': records,
                  'source_manifest_sha256': 'b' * 64, 'review_state_sha256': 'c' * 64,
                  'feedback_current': True, 'created_at': timestamp}
        (bundle_dir / 'bundle.json').write_text(json.dumps(bundle))

    write_bundle('first')
    first = archive.capture(tmp_path, bundle_dir, review_dir, 'fixture.vrt')
    root = archive.archive_root(tmp_path)
    assert json.loads((root/'latest'/f'{archive.workspace_status_key("ussfo", "fixture")}.json').read_text()) == first
    original = archive._read_revision(root, first['revision'])
    assert archive.capture(tmp_path, bundle_dir, review_dir, 'fixture.vrt') == first
    tags = review_dir / 'chip-tags.json'
    tags.write_text(json.dumps({'schema_version': 1, 'chips': {'chip': {'tags': ['setting:parkland']}}}))
    tagged = archive.capture(tmp_path, bundle_dir, review_dir, 'fixture.vrt')
    assert tagged['revision'] != first['revision']
    assert tagged['chip_tags_sha256'] == archive.digest(tags)
    assert archive._read_revision(root, tagged['revision'])['chip-tags.json'] == tags.read_bytes()
    write_bundle('second')
    second = archive.capture(tmp_path, bundle_dir, review_dir, 'fixture.vrt')
    assert second['revision'] != first['revision']
    assert archive._read_revision(root, first['revision']) == original
    restored = archive._read_revision(root, second['revision'])
    assert json.loads(restored['reviews.json'])['saved_at'] == 'second'
    for name, sha in second['snapshot_files_sha256'].items():
        assert hashlib.sha256(restored[name]).hexdigest() == sha
    corrupt = dict(restored, **{'reviews.json': b'changed'})
    with pytest.raises(ValueError, match='Immutable revision descriptor differs'):
        archive._store_revision(root, second['revision'], corrupt)


def test_unopened_legacy_nyc_workspace_is_not_backed_up(tmp_path):
    annotations = tmp_path / 'annotations'
    legacy = 'usnyc-2022-cold-start-30cm'
    review_id = legacy + '-development'
    path = tmp_path / 'qa/registration' / legacy / 'manifest.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'metadata': {'review_id': review_id,
                                             'inventory': '/inventory/usnyc/inventory.parquet'}}))
    assert [city for city, _, _ in archive.backup_workspaces(tmp_path, annotations)] == ['ussfo', 'usbos']
    bundle = annotations / 'usnyc' / review_id / 'bundle.json'
    bundle.parent.mkdir(parents=True)
    bundle.write_text('{}')
    assert [city for city, _, _ in archive.backup_workspaces(tmp_path, annotations)] == ['ussfo', 'usbos', 'usnyc']


def test_temporal_backup_discovery_includes_initialized_years_and_rejects_escape(tmp_path):
    annotations=tmp_path/'annotations'
    records=[]
    for year in (2021,2023,2025):
        name='usbos-2023-external' if year==2023 else f'usbos-naip-{year}'
        directory=tmp_path/'qa/registration'/name;directory.mkdir(parents=True)
        rid=f'boston-{year}'
        (directory/'manifest.json').write_text(json.dumps({'metadata':{'review_id':rid}}))
        records.append(dict(id=rid,city='usbos',state='ready',review_dir=str(directory),raster=str(tmp_path/f'{year}.vrt')))
        if year!=2025:
            bundle=annotations/'usbos'/rid/'bundle.json';bundle.parent.mkdir(parents=True);bundle.write_text('{}')
    registry=tmp_path/'qa/temporal-acquisitions.json'
    registry.write_text(json.dumps({'acquisitions':records}))
    found=archive.backup_workspaces(tmp_path,annotations)
    assert [review for city,review,_ in found if city=='usbos']==['usbos-2023-external','usbos-naip-2021']
    records[0]['raster']=str(tmp_path.parent/'outside.vrt')
    registry.write_text(json.dumps({'acquisitions':records}))
    with pytest.raises(ValueError,match='escapes'):archive.backup_workspaces(tmp_path,annotations)


def test_backup_status_is_scoped_to_review_year_with_matching_legacy_fallback(tmp_path):
    root=archive.archive_root(tmp_path)
    def put(folder,key,value):archive._write_json_atomic(root/folder/f'{key}.json',value)
    put('errors','usbos',{'error':'Another acquisition failed'})
    put('remote-status','usbos',{'review_id':'2023','review_state_sha256':'old'})
    put('errors',archive.workspace_status_key('usbos','2025'),{'error':'2025 failed'})
    put('remote-status',archive.workspace_status_key('usbos','2025'),{'review_id':'2025','review_state_sha256':'new'})
    old=archive.status(tmp_path,'usbos','old',False,review_id='2023')
    new=archive.status(tmp_path,'usbos','new',False,review_id='2025')
    absent=archive.status(tmp_path,'usbos','old',False,review_id='2021')
    assert old['archive_error'] is None and old['remote']['current']
    assert new['archive_error']=='2025 failed' and new['remote']['current']
    assert not absent['remote']['current']


def test_history_pack_reuses_shared_indexes_without_losing_dependencies(tmp_path, monkeypatch):
    import shutil
    from pathlib import Path
    import zipfile

    directory = tmp_path / 'archive'
    shared = {'manifest.json': b'{"shared":true}', 'reviews.json': b'{"reviews":{}}'}
    for revision in ('a' * 64, 'b' * 64):
        archive._store_revision(directory, revision, dict(shared, **{'archive.json': revision.encode()}))
    stage = tmp_path / 'stage'
    shutil.copytree(directory / 'revisions-v2', stage)
    reads = []
    validated = []
    original_read = Path.read_text
    original_valid = archive._valid_sha
    def read_text(path, *args, **kwargs):
        if path.parent.name == 'file-index':
            reads.append(path.name)
        return original_read(path, *args, **kwargs)
    def valid_sha(value):
        validated.append(value)
        return original_valid(value)
    monkeypatch.setattr(Path, 'read_text', read_text)
    monkeypatch.setattr(archive, '_valid_sha', valid_sha)
    timestamps = {path.name: path.stat().st_mtime_ns for path in stage.glob('*.json')}
    archive._pack_history(directory, stage)
    assert len(reads) == len(set(reads)) == 4
    assert len(validated) == len(set(validated))
    for descriptor in stage.glob('*.json'):
        assert descriptor.stat().st_mtime_ns == timestamps[descriptor.name]
        record = json.loads(descriptor.read_text())
        packed = set()
        for pack in record['packs']:
            with zipfile.ZipFile(directory / 'packs' / (pack + '.zip')) as contents:
                packed.update(name.removesuffix('.zlib') for name in contents.namelist())
        for entry in record['files'].values():
            index = json.loads((directory / 'file-index' / (entry['index_sha256'] + '.json')).read_text())
            assert set(index['chunks']) <= packed
    assert archive._read_revision(directory, 'a' * 64)['manifest.json'] == shared['manifest.json']


def test_history_pack_rejects_unsafe_chunk_ids(tmp_path):
    directory = tmp_path / 'archive'
    stage = tmp_path / 'stage'
    directory.mkdir()
    stage.mkdir()
    (stage / 'revision.json').write_text(json.dumps({'files': {'reviews.json': {'chunks': ['../escape']}}}))
    with pytest.raises(ValueError, match='Unsafe chunk ID'):
        archive._pack_history(directory, stage)


def test_staged_revision_sync_compares_content_instead_of_timestamps(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(archive, '_pack_history', lambda directory, stage: None)
    monkeypatch.setattr(archive, 'command', lambda *args: calls.append(args))
    archive._sync_history(tmp_path / 'archive', 'gs://test-archive')
    assert len(calls) == 5
    for arguments in calls:
        assert ('--checksums-only' in arguments) == arguments[4].endswith('/revisions-v2')


@pytest.mark.skipif(archive.os.name != 'nt', reason='Windows SDK worker settings')
def test_backup_cloud_worker_limit_is_scoped_to_child_process(monkeypatch):
    seen = []
    monkeypatch.setattr(archive.subprocess, 'check_output', lambda *args, **kwargs: seen.append(kwargs) or 'ok\n')
    before = dict(archive.os.environ)
    assert archive.command('gcloud.cmd', 'storage', 'ls') == 'ok'
    assert seen[0]['env']['CLOUDSDK_STORAGE_PROCESS_COUNT'] == '1'
    assert seen[0]['env']['CLOUDSDK_STORAGE_THREAD_COUNT'] == '2'
    assert dict(archive.os.environ) == before
    archive.command('git', 'status')
    assert seen[1]['env'] == before
    archive.command('gcloud.cmd', 'storage', 'rsync', '--checksums-only')
    assert seen[2]['env']['CLOUDSDK_STORAGE_USE_GCLOUD_CRC32C'] == 'false'
    assert seen[2]['env']['CLOUDSDK_STORAGE_CHECK_HASHES'] == 'always'
    assert dict(archive.os.environ) == before


def test_backup_status_uses_latest_external_progress(tmp_path):
    from urban_tree_ml.backup_job import BackupJob

    job = BackupJob()
    job.error = 'Previous UI backup failed'
    directory = archive.archive_root(tmp_path)
    archive._progress(directory, 'Syncing file-index')
    assert job.status(tmp_path)['error'] is None
    archive._progress(directory, 'Failed', error='Current CLI transfer failed', diagnostics='receipt.json')
    assert job.status(tmp_path)['error'] == 'Current CLI transfer failed'
    assert job.status(tmp_path)['diagnostics'] == 'receipt.json'
    archive._progress(directory, 'Complete')
    assert job.status(tmp_path)['error'] is None


def test_backup_preparation_failure_is_persisted(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from urban_tree_ml import backup_job

    def fail_preparation():
        raise ValueError('Snapshot preparation failed')

    monkeypatch.setattr(backup_job, 'Thread', lambda target, **kwargs: SimpleNamespace(start=target))
    job = backup_job.BackupJob(before_backup=fail_preparation)
    assert job.start(tmp_path, tmp_path / 'annotations')
    status = job.status(tmp_path)
    assert not status['running']
    assert status['stage'] == 'Failed'
    assert status['error'] == 'Snapshot preparation failed'


def test_backup_job_preserves_transfer_diagnostics(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from urban_tree_ml import backup_job

    def fail_transfer(root, annotations, bucket):
        archive._progress(archive.archive_root(root), 'Failed', error='Transfer failed', diagnostics='receipt.json')
        raise ValueError('Transfer failed')

    monkeypatch.setattr(backup_job, 'backup', fail_transfer)
    monkeypatch.setattr(backup_job, 'Thread', lambda target, **kwargs: SimpleNamespace(start=target))
    job = backup_job.BackupJob()
    assert job.start(tmp_path, tmp_path / 'annotations')
    assert job.status(tmp_path)['diagnostics'] == 'receipt.json'


def test_backup_preparation_failure_preserves_active_external_job(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from urban_tree_ml import backup_job

    def concurrent_backup():
        directory = archive.archive_root(tmp_path)
        (directory / 'backup.lock').write_text('external owner')
        archive._progress(directory, 'Syncing file-index')
        raise ValueError('Snapshot preparation failed')

    monkeypatch.setattr(backup_job, 'Thread', lambda target, **kwargs: SimpleNamespace(start=target))
    job = backup_job.BackupJob(before_backup=concurrent_backup)
    assert job.start(tmp_path, tmp_path / 'annotations')
    status = job.status(tmp_path)
    assert status['running']
    assert status['stage'] == 'Syncing file-index'
    assert status['error'] is None


def test_atomic_receipt_retries_windows_reader_lock(tmp_path, monkeypatch):
    target = tmp_path / 'progress.json'
    target.write_text('{"old": true}')
    original = archive.os.replace
    calls = []
    def replace(source, destination):
        calls.append(1)
        if len(calls) < 3:
            assert json.loads(target.read_text()) == {'old': True}
            raise PermissionError('reader holds file')
        return original(source, destination)
    monkeypatch.setattr(archive.os, 'replace', replace)
    monkeypatch.setattr(archive.time, 'sleep', lambda _: None)
    if archive.os.name != 'nt':
        pytest.skip('Windows sharing violation')
    archive._write_json_atomic(target, {'new': True})
    assert len(calls) == 3
    assert json.loads(target.read_text()) == {'new': True}
    assert not list(tmp_path.glob('*.tmp'))
