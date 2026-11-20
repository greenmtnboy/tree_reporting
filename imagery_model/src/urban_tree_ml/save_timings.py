"""Bounded, annotation-free diagnostics for local reviewer save latency."""
from collections import deque
from datetime import datetime, timezone
from threading import Lock
from time import perf_counter


class SaveTimings:
    def __init__(self, limit=30):
        self._recent = deque(maxlen=limit)
        self._lock = Lock()

    def record(self, city, route, outcome, started, phases):
        item = dict(at=datetime.now(timezone.utc).isoformat(), city=city,
                    route=route, outcome=outcome,
                    total_ms=round((perf_counter()-started)*1000, 2),
                    phases_ms={k: round(v, 2) for k, v in phases.items()})
        with self._lock:
            self._recent.append(item)
        return item

    def recent(self):
        with self._lock:
            return [{**item, 'phases_ms': dict(item['phases_ms'])} for item in self._recent]


class PhaseTimer:
    def __init__(self, phases):
        self.phases = phases
        self.last = perf_counter()

    def mark(self, name):
        now = perf_counter()
        self.phases[name] = (now-self.last)*1000
        self.last = now
