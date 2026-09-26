"""Coalesced snapshots of durable reviews, outside the interactive save lock."""
import shutil
import tempfile
import time
from pathlib import Path
from threading import Condition, Lock, Thread

from urban_tree_ml.feedback import snapshot_registration_annotations


class SnapshotWorker:
    def __init__(self, contexts, review_lock, debounce=.75):
        self.contexts, self.review_lock, self.debounce = contexts, review_lock, debounce
        self.condition = Condition()
        self.snapshot_lock = Lock()
        self.requested, self.completed, self.errors = {}, {}, {}
        self.running = None
        self.stopping = False
        self.thread = Thread(target=self._run, name='curation-snapshots', daemon=True)
        self.thread.start()

    def request(self, city):
        with self.condition:
            self.requested[city] = self.requested.get(city, 0) + 1
            self.errors.pop(city, None)
            self.condition.notify_all()

    def status(self, city):
        with self.condition:
            return {'pending': self.requested.get(city, 0) > self.completed.get(city, 0),
                    'running': self.running == city, 'error': self.errors.get(city)}

    def flush(self, timeout=120):
        deadline = time.monotonic() + timeout
        with self.condition:
            targets = dict(self.requested)
            while any(self.completed.get(c, 0) < v for c, v in targets.items()):
                errors = [self.errors[c] for c in targets if c in self.errors]
                if errors:
                    raise RuntimeError('Annotation snapshot failed: ' + '; '.join(errors))
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('Annotation snapshot is still pending; retry backup shortly')
                self.condition.wait(remaining)

    def close(self):
        with self.condition:
            self.stopping = True
            self.condition.notify_all()
        self.thread.join(timeout=30)

    def _capture(self, context):
        # Lock order matches explicit publication: snapshot lock, then review lock.
        with self.snapshot_lock:
            with tempfile.TemporaryDirectory(prefix='curation-snapshot-') as temporary:
                stage = Path(temporary)
                with self.review_lock:
                    for name in ['manifest.json', 'reviews.json', 'training-feedback.json']:
                        source = context.directory / name
                        if source.exists():
                            shutil.copy2(source, stage / name)
                snapshot_registration_annotations(context.config, context.raster,
                    review_dir=stage, archive_review_dir=context.directory)

    def _run(self):
        while True:
            with self.condition:
                pending = [c for c,v in self.requested.items()
                           if v > self.completed.get(c, 0) and c not in self.errors]
                if not pending:
                    if self.stopping:
                        return
                    self.condition.wait()
                    continue
                if not self.stopping:
                    self.condition.wait(self.debounce)
                city = pending[0]
                generation = self.requested[city]
                self.running = city
            try:
                self._capture(self.contexts[city])
            except Exception as error:
                with self.condition:
                    # A subsequent edit can retry; don't suppress a newer request.
                    if self.requested[city] == generation:
                        self.errors[city] = str(error)
            else:
                with self.condition:
                    self.completed[city] = generation
                    self.errors.pop(city, None)
            finally:
                with self.condition:
                    self.running = None
                    self.condition.notify_all()
