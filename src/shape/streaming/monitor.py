from dataclasses import dataclass

from shape.drift import compare
from shape.streaming.online import OnlineShape


@dataclass(frozen=True, slots=True)
class MonitorEvent:
    rows_seen: int
    max_drift: float
    drifts: tuple


class ShapeMonitor:
    def __init__(self, reference, every=1000, max_buffer=10000):
        if every < 1:
            raise ValueError("every")
        self.reference = reference
        self.every = every
        self.online = OnlineShape(max_buffer)

    def add(self, row):
        self.online.add(row)
        if self.online.total % self.every:
            return None
        d = tuple(compare(self.reference, self.online.snapshot()))
        return MonitorEvent(self.online.total, max((x.score for x in d), default=0), d)
