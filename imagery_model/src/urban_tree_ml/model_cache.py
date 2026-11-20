"""Byte-budgeted model retention; eviction never mutates a live request's bundle."""
from collections import OrderedDict
from threading import RLock
import sys

import numpy as np
import polars as pl


def retained_bytes(value, seen=None):
    """Conservative payload estimate, not a promise about process RSS."""
    seen = set() if seen is None else seen
    if id(value) in seen:
        return 0
    seen.add(id(value))
    if isinstance(value, pl.DataFrame):
        return value.estimated_size()
    if isinstance(value, np.ndarray):
        return int(value.nbytes) + 128
    size = sys.getsizeof(value)
    if isinstance(value, dict):
        return size + sum(retained_bytes(k, seen) + retained_bytes(v, seen)
                          for k, v in list(value.items()))
    if isinstance(value, (list, tuple, set)):
        return size + sum(retained_bytes(v, seen) for v in value)
    return size


class ModelBundleCache:
    def __init__(self, budget_bytes):
        if budget_bytes < 0:
            raise ValueError('Model cache budget must be non-negative')
        self.budget_bytes = budget_bytes
        self.entries = OrderedDict()
        self.lock = RLock()

    @property
    def size_bytes(self):
        return sum(sum(parts.values()) for _, parts in self.entries.values())

    def get_or_load(self, key, loader):
        # Single-flight loading prevents concurrent cards loading the same large
        # parquet bundle repeatedly. Requests keep their own references on return.
        with self.lock:
            if key in self.entries:
                self.entries.move_to_end(key)
                return self.entries[key][0]
            bundle, parts = loader()
            if sum(parts.values()) <= self.budget_bytes and self.budget_bytes:
                self.entries[key] = (bundle, parts)
                self._trim()
            return bundle

    def resize(self, key, bundle, component, size):
        with self.lock:
            entry = self.entries.get(key)
            # An evicted in-flight request must not resize a replacement bundle.
            if entry is None or entry[0] is not bundle:
                return
            entry[1][component] = size
            if sum(entry[1].values()) > self.budget_bytes:
                del self.entries[key]
            else:
                self.entries.move_to_end(key)
                self._trim()

    def _trim(self):
        while self.size_bytes > self.budget_bytes:
            self.entries.popitem(last=False)
