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
    def __init__(self, size: timedelta, allowed_lateness: timedelta = timedelta(0)) -> None:
        if size.total_seconds() <= 0:
            raise ValueError("size must be positive")
        if allowed_lateness.total_seconds() < 0:
            raise ValueError("lateness cannot be negative")
        self.size = size
        self.allowed_lateness = allowed_lateness
        self._windows: defaultdict[datetime, list[Any]] = defaultdict(list)
        self._max_event_time: datetime | None = None
        self.late_dropped = 0

    def _start(self, ts: datetime) -> datetime:
        if ts.tzinfo is None:
            raise ValueError("event time must be timezone-aware")
        epoch = ts.timestamp()
        n = self.size.total_seconds()
        return datetime.fromtimestamp((epoch // n) * n, tz=UTC)

    @property
    def watermark(self) -> datetime | None:
        return (
            None if self._max_event_time is None else self._max_event_time - self.allowed_lateness
        )

    def add(self, ts: datetime, value: Any) -> bool:
        if self.watermark is not None and ts < self.watermark:
            self.late_dropped += 1
            return False
        if self._max_event_time is None or ts > self._max_event_time:
            self._max_event_time = ts
        self._windows[self._start(ts)].append(value)
        return True

    def close_ready(self) -> list[WindowResult]:
        wm = self.watermark
        if wm is None:
            return []
        ready: list[WindowResult] = []
        for start in sorted(list(self._windows)):
            end = start + self.size
            if end <= wm:
                ready.append(WindowResult(start, end, tuple(self._windows.pop(start))))
        return ready

    def snapshot(self) -> dict[str, Any]:
        return {
            "size_seconds": self.size.total_seconds(),
            "lateness_seconds": self.allowed_lateness.total_seconds(),
            "max_event_time": None
            if self._max_event_time is None
            else self._max_event_time.isoformat(),
            "late_dropped": self.late_dropped,
            "windows": {k.isoformat(): v for k, v in self._windows.items()},
        }

    @classmethod
    def restore(cls, snapshot: dict[str, Any]) -> TumblingWindow:
        """Rebuild a window from ``snapshot()``: same open windows, watermark and late count."""
        window = cls(
            timedelta(seconds=snapshot["size_seconds"]),
            timedelta(seconds=snapshot["lateness_seconds"]),
        )
        newest = snapshot["max_event_time"]
        window._max_event_time = None if newest is None else datetime.fromisoformat(newest)
        window.late_dropped = int(snapshot["late_dropped"])
        for start, values in snapshot["windows"].items():
            window._windows[datetime.fromisoformat(start)] = list(values)
        return window
