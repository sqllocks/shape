"""Incremental bounded streaming capture and periodic Shape snapshots."""

from __future__ import annotations

from collections import deque

from shape.capture import capture_rows


class OnlineShape:
    def __init__(self, max_buffer=10000):
        if max_buffer < 1:
            raise ValueError("max_buffer")
        self.max_buffer = max_buffer
        self.buffer = deque(maxlen=max_buffer)
        self.total = 0

    def add(self, row):
        self.total += 1
        self.buffer.append(dict(row))

    def snapshot(self):
        s = capture_rows(self.buffer).to_dict()
        s["stream_rows_seen"] = self.total
        s["window_rows"] = len(self.buffer)
        return s
