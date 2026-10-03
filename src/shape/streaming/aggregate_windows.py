"""Bounded-state event-time aggregate windows."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta

from .windows import window_start


@dataclass(slots=True)
class NumericAggregate:
    count: int = 0
    null_count: int = 0
    total: float = 0.0
    minimum: float | None = None
    maximum: float | None = None

    def add(self, v):
        self.count += 1
        if v is None:
            self.null_count += 1
            return
        x = float(v)
        self.total += x
        self.minimum = x if self.minimum is None else min(self.minimum, x)
        self.maximum = x if self.maximum is None else max(self.maximum, x)

    def summary(self):
        return {
            "count": self.count,
            "null_count": self.null_count,
            "mean": None
            if self.count == self.null_count
            else self.total / (self.count - self.null_count),
            "min": self.minimum,
            "max": self.maximum,
        }


@dataclass(frozen=True, slots=True)
class AggregateWindowResult:
    start: datetime
    end: datetime
    summary: dict


class AggregateTumblingWindow:
    def __init__(self, size: timedelta, allowed_lateness: timedelta = timedelta(0)):
        if size.total_seconds() <= 0 or allowed_lateness.total_seconds() < 0:
            raise ValueError("invalid window")
        self.size = size
        self.allowed_lateness = allowed_lateness
        self._windows = defaultdict(NumericAggregate)
        self._max = None
        self.late_dropped = 0

    def _start(self, ts):
        return window_start(ts, self.size)

    @property
    def watermark(self):
        return None if self._max is None else self._max - self.allowed_lateness

    def add(self, ts, v):
        if self.watermark is not None and ts < self.watermark:
            self.late_dropped += 1
            return False
        if self._max is None or ts > self._max:
            self._max = ts
        self._windows[self._start(ts)].add(v)
        return True

    def close_ready(self):
        wm = self.watermark
        if wm is None:
            return []
        out = []
        for start in sorted(list(self._windows)):
            end = start + self.size
            if end <= wm:
                out.append(AggregateWindowResult(start, end, self._windows.pop(start).summary()))
        return out
