from threading import Event

from urban_tree_ml.backup_job import BackupJob


def test_manual_backup_nonblocking_and_exclusive(tmp_path, monkeypatch):
    entered, release, finished = Event(), Event(), Event()

    def fake_backup(*args):
        entered.set()
        release.wait(5)
        finished.set()

    monkeypatch.setattr("urban_tree_ml.backup_job.backup", fake_backup)
    job = BackupJob()
    assert job.start(tmp_path, tmp_path)
    assert entered.wait(2)
    assert job.status(tmp_path)["running"]
    assert not job.start(tmp_path, tmp_path)
    release.set()
    assert finished.wait(2)


def test_manual_backup_respects_existing_worker(tmp_path):
    archive = tmp_path / "curation-archive"
    archive.mkdir()
    (archive / "backup.lock").touch()
    job = BackupJob()
    assert not job.start(tmp_path, tmp_path)
    assert job.status(tmp_path)["running"]


def test_backup_waits_for_pending_snapshots(tmp_path, monkeypatch):
    waiting, release, uploaded = Event(), Event(), Event()
    def flush():
        waiting.set()
        assert release.wait(3)
    monkeypatch.setattr('urban_tree_ml.backup_job.backup',lambda *args:uploaded.set())
    job=BackupJob(before_backup=flush)
    assert job.start(tmp_path,tmp_path)
    assert waiting.wait(2) and not uploaded.is_set()
    release.set()
    assert uploaded.wait(2)
