"""Non-blocking, manually triggered Studio backup job."""

from threading import Lock, Thread
import json

from urban_tree_ml.curation_archive import archive_root, backup, _progress


class BackupJob:
    def __init__(self, before_backup=None, snapshot_lock=None):
        self.lock = Lock()
        self.running = False
        self.error = None
        self.before_backup, self.snapshot_lock = before_backup, snapshot_lock

    def status(self, root):
        with self.lock:
            progress = archive_root(root) / 'progress.json'
            latest = json.loads(progress.read_text()) if progress.exists() else {}
            return {
                **latest,
                "running": self.running or (archive_root(root) / "backup.lock").exists(),
                "error": latest.get('error') if latest else self.error,
            }

    def start(self, root, annotations):
        with self.lock:
            if self.running or (archive_root(root) / "backup.lock").exists():
                return False
            self.running = True
            self.error = None

        def run():
            try:
                _progress(archive_root(root), 'Waiting for saved annotation snapshots')
                if self.before_backup:
                    self.before_backup()
                if self.snapshot_lock is None:
                    backup(root, annotations, "gs://arborary-world-curation-archive")
                else:
                    backup(root, annotations, "gs://arborary-world-curation-archive", capture_lock=self.snapshot_lock)
            except Exception as error:
                with self.lock:
                    self.error = str(error)
                # Snapshot preparation can fail before backup() records its own failure.
                # Preserve any transfer diagnostics already written by backup().
                directory = archive_root(root)
                if not (directory / 'backup.lock').exists() and self.status(root).get('stage') != 'Failed':
                    _progress(archive_root(root), 'Failed', error=str(error))
            finally:
                with self.lock:
                    self.running = False

        Thread(target=run, daemon=False, name="curation-backup").start()
        return True
