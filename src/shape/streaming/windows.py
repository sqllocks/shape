"""Event-time windows with bounded lateness."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any


@dataclass(frozen=True, slots=True)
class WindowResult:
    start: datetime
    end: datetime
    values: tuple[Any, ...]


class TumblingWindow:
    def __init__(self, size: timedelta, allowed_lateness: timedelta = timedelta(0)):
        if size.total_seconds() <= 0:
            raise ValueError("size must be positive")
        if allowed_lateness.total_seconds() < 0:
            raise ValueError("lateness cannot be negative")
        self.size = size
        self.allowed_lateness = allowed_lateness
        self._windows = defaultdict(list)
        self._max_event_time = None
        self.late_dropped = 0

    def _start(self, ts: datetime):
        if ts.tzinfo is None:
            raise ValueError("event time must be timezone-aware")
        epoch = ts.timestamp()
        n = self.size.total_seconds()
        return datetime.fromtimestamp((epoch // n) * n, tz=UTC)

    @property
    def watermark(self):
        return (
            None if self._max_event_time is None else self._max_event_time - self.allowed_lateness
        )

    def add(self, ts: datetime, value: Any):
        if self.watermark is not None and ts < self.watermark:
            self.late_dropped += 1
            return False
        if self._max_event_time is None or ts > self._max_event_time:
            self._max_event_time = ts
        self._windows[self._start(ts)].append(value)
        return True

    def close_ready(self):
        wm = self.watermark
        if wm is None:
            return []
        ready = []
        for start in sorted(list(self._windows)):
            end = start + self.size
            if end <= wm:
                ready.append(WindowResult(start, end, tuple(self._windows.pop(start))))
        return ready

    def snapshot(self):
        return {
            "size_seconds": self.size.total_seconds(),
            "lateness_seconds": self.allowed_lateness.total_seconds(),
            "max_event_time": None
            if self._max_event_time is None
            else self._max_event_time.isoformat(),
            "late_dropped": self.late_dropped,
            "windows": {k.isoformat(): v for k, v in self._windows.items()},
        }
