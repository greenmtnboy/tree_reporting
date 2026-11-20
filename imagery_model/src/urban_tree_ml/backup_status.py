"""Cheap status reads over immutable review-collection snapshots."""
from threading import Lock

from urban_tree_ml.feedback import _json_sha256


class ManifestDigests:
    def __init__(self):
        self._entries = {}
        self._lock = Lock()

    def get(self, directory, manifest):
        # read_collection reuses the manifest object across review-only saves,
        # and replaces it when its file changes. Hold a reference, not just id().
        # Call outside review_state_lock: a cold hash must not lock out saves.
        key = str(directory)
        with self._lock:
            previous = self._entries.get(key)
            if previous is not None and previous[0] is manifest:
                return previous[1]
            digest = _json_sha256(manifest)
            self._entries.pop(key, None)
            self._entries[key] = (manifest, digest)
            while len(self._entries) > 4:
                self._entries.pop(next(iter(self._entries)))
            return digest
