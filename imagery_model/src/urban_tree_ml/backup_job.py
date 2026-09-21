"""Non-blocking, manually triggered Studio backup job."""

from threading import Lock, Thread

from urban_tree_ml.curation_archive import archive_root, backup


class BackupJob:
    def __init__(self, before_backup=None, snapshot_lock=None):
        self.lock = Lock()
        self.running = False
        self.error = None
        self.before_backup, self.snapshot_lock = before_backup, snapshot_lock

    def status(self, root):
        with self.lock:
            return {
                "running": self.running or (archive_root(root) / "backup.lock").exists(),
                "error": self.error,
            }

    def start(self, root, annotations):
        with self.lock:
            if self.running or (archive_root(root) / "backup.lock").exists():
                return False
            self.running = True
            self.error = None

        def run():
            try:
                if self.before_backup:
                    self.before_backup()
                if self.snapshot_lock is None:
                    backup(root, annotations, "gs://arborary-world-curation-archive")
                else:
                    backup(root, annotations, "gs://arborary-world-curation-archive", capture_lock=self.snapshot_lock)
            except Exception as error:
                with self.lock:
                    self.error = str(error)
            finally:
                with self.lock:
                    self.running = False

        Thread(target=run, daemon=False, name="curation-backup").start()
        return True
