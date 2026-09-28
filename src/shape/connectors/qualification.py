"""
Connector qualification primitives: ordering, dedupe, partition checkpoints, reconnect and
replay.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ConnectorRecord:
    partition: str
    offset: int
    value: object
    message_id: str | None = None


class PartitionCheckpointStore:
    def __init__(self):
        self.offsets = {}

    def committed(self, partition):
        return self.offsets.get(str(partition), -1)

    def commit(self, partition, offset):
        p = str(partition)
        o = int(offset)
        if o > self.committed(p):
            self.offsets[p] = o


class ExactlyOnceProjector:
    def __init__(self, store=None):
        self.store = store or PartitionCheckpointStore()
        self.ids = set()

    def process(self, records, handler):
        accepted = duplicates = stale = 0
        for r in records:
            if r.offset <= self.store.committed(r.partition):
                stale += 1
                continue
            if r.message_id is not None and r.message_id in self.ids:
                duplicates += 1
                self.store.commit(r.partition, r.offset)
                continue
            handler(r.value)
            if r.message_id is not None:
                self.ids.add(r.message_id)
            self.store.commit(r.partition, r.offset)
            accepted += 1
        return {
            "accepted": accepted,
            "duplicates": duplicates,
            "stale": stale,
            "offsets": dict(self.store.offsets),
        }


def reconnecting_batches(connect, max_attempts=5):
    attempts = 0
    while True:
        try:
            yield from connect()
            return
        except Exception:
            attempts += 1
            if attempts >= max_attempts:
                raise
