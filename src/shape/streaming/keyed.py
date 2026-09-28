"""Bounded keyed partition state with event-time TTL."""

from __future__ import annotations

import heapq
from dataclasses import dataclass


@dataclass
class _Entry:
    value: object
    expires_at: float
    version: int


class KeyedState:
    def __init__(self, ttl_seconds: float, max_keys: int = 100000):
        if ttl_seconds <= 0 or max_keys < 1:
            raise ValueError("invalid state bounds")
        self.ttl = ttl_seconds
        self.max_keys = max_keys
        self._d = {}
        self._heap = []
        self._version = 0

    def _expire(self, now):
        while self._heap and self._heap[0][0] <= now:
            exp, ver, key = heapq.heappop(self._heap)
            e = self._d.get(key)
            if e is not None and e.version == ver and e.expires_at <= now:
                self._d.pop(key, None)

    def put(self, key, value, event_time: float):
        self._expire(event_time)
        self._version += 1
        e = _Entry(value, event_time + self.ttl, self._version)
        self._d[key] = e
        heapq.heappush(self._heap, (e.expires_at, e.version, key))
        while len(self._d) > self.max_keys:
            exp, ver, k = heapq.heappop(self._heap)
            e2 = self._d.get(k)
            if e2 is not None and e2.version == ver:
                self._d.pop(k, None)

    def get(self, key, now: float):
        self._expire(now)
        e = self._d.get(key)
        return None if e is None else e.value

    def snapshot(self):
        return {
            "ttl_seconds": self.ttl,
            "max_keys": self.max_keys,
            "items": [(k, e.value, e.expires_at) for k, e in self._d.items()],
        }

    @classmethod
    def restore(cls, s):
        x = cls(s["ttl_seconds"], s["max_keys"])
        for k, v, exp in s["items"]:
            x._version += 1
            e = _Entry(v, exp, x._version)
            x._d[k] = e
            heapq.heappush(x._heap, (exp, e.version, k))
        return x

    def __len__(self):
        return len(self._d)


class PartitionedKeyedState:
    """Deterministic keyed state sharded by stable key hash."""

    def __init__(self, partitions: int, ttl_seconds: float, max_keys_per_partition: int = 100000):
        if partitions < 1:
            raise ValueError("partitions")
        self.partitions = partitions
        self.states = [KeyedState(ttl_seconds, max_keys_per_partition) for _ in range(partitions)]

    def partition_for(self, key):
        import hashlib

        b = repr((type(key).__name__, key)).encode("utf-8")
        return int.from_bytes(hashlib.blake2b(b, digest_size=8).digest(), "big") % self.partitions

    def put(self, key, value, event_time):
        self.states[self.partition_for(key)].put(key, value, event_time)

    def get(self, key, now):
        return self.states[self.partition_for(key)].get(key, now)

    def snapshot(self):
        return {"partitions": self.partitions, "states": [s.snapshot() for s in self.states]}

    @classmethod
    def restore(cls, s):
        obj = cls.__new__(cls)
        obj.partitions = s["partitions"]
        obj.states = [KeyedState.restore(x) for x in s["states"]]
        return obj

    def __len__(self):
        return sum(map(len, self.states))
