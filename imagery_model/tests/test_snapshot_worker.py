from pathlib import Path
from threading import Event, Lock
from types import SimpleNamespace

import pytest

from urban_tree_ml.snapshot_worker import SnapshotWorker


def test_archive_work_does_not_hold_save_lock_and_new_edits_are_captured(tmp_path, monkeypatch):
    directory = tmp_path/'reviews'; directory.mkdir()
    for name in ['manifest.json', 'reviews.json']:
        (directory/name).write_text('first')
    entered, release = Event(), Event()
    versions = []
    def snapshot(config, raster, *, review_dir, archive_review_dir):
        versions.append((Path(review_dir)/'reviews.json').read_text())
        assert archive_review_dir == directory
        if len(versions)==1:
            entered.set()
            assert release.wait(5)
    monkeypatch.setattr('urban_tree_ml.snapshot_worker.snapshot_registration_annotations',snapshot)
    lock=Lock()
    worker=SnapshotWorker({'city':SimpleNamespace(directory=directory, config=None, raster=None)},lock,debounce=0)
    try:
        worker.request('city'); assert entered.wait(2)
        assert lock.acquire(timeout=.1), 'archive held interactive lock'
        try:
            (directory/'reviews.json').write_text('second')
            worker.request('city')
        finally:
            lock.release()
        release.set();worker.flush(timeout=5)
        assert versions==['first','second']
        assert not worker.status('city')['pending']
    finally:
        release.set();worker.close()


def test_snapshot_failure_is_reported_and_next_request_retries(tmp_path, monkeypatch):
    worker=SnapshotWorker({'city':None},Lock(),debounce=0)
    def fail(context):
        raise OSError('disk unavailable')
    monkeypatch.setattr(worker,'_capture',fail)
    try:
        worker.request('city')
        with pytest.raises(RuntimeError,match='disk unavailable'):
            worker.flush(timeout=3)
        assert worker.status('city')['pending']
        monkeypatch.setattr(worker,'_capture',lambda context:None)
        worker.request('city');worker.flush(timeout=3)
        assert worker.status('city')=={'pending':False,'running':False,'error':None}
    finally:
        worker.close()


def test_unchanged_json_is_not_rewritten_and_image_cache_invalidates(tmp_path):
    from urban_tree_ml.feedback import _write_json_atomic
    from urban_tree_ml.curation_archive import image_digest
    path=tmp_path/'record.json'
    _write_json_atomic(path,{'a':1});modified=path.stat().st_mtime_ns
    _write_json_atomic(path,{'a':1});assert path.stat().st_mtime_ns==modified
    first=image_digest(path)
    _write_json_atomic(path,{'a':2})
    assert image_digest(path)!=first
    assert image_digest(path)==image_digest(path,verify=True)
