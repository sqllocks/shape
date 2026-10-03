"""The progress line of ``shape emit`` and ``shape stream`` (W2-09): one line on standard error,

::

    emitted 120,400 / 1,000,000 events  9,980/s  lag 0.4s  retries 2  dead-lettered 0  eta 1m28s

rewritten at most once a second, and a final line at exit. Nothing is written to standard output.
The total and the ETA are left out when they are not known. On a terminal the line is rewritten in
place (a carriage return, padded over a longer previous line); on a pipe each update is a line of
its own.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TextIO


def format_eta(seconds: float) -> str:
    """``45s``, ``1m28s``, ``2h03m``."""
    whole = max(0, round(seconds))
    if whole < 60:
        return f"{whole}s"
    if whole < 3600:
        return f"{whole // 60}m{whole % 60:02d}s"
    return f"{whole // 3600}h{whole % 3600 // 60:02d}m"


class ProgressLine:
    """Writes the progress line to ``stream`` (``tty``: rewrite in place). ``clock`` is for tests;
    ``interval`` is the least number of seconds between two writes; ``start_offset`` is the
    position the run began at (a resume), so the rate counts only what this run delivered."""

    def __init__(
        self,
        stream: TextIO,
        *,
        tty: bool = False,
        clock: Callable[[], float] = time.monotonic,
        interval: float = 1.0,
        start_offset: int = 0,
    ) -> None:
        self.stream = stream
        self.tty = tty
        self._clock = clock
        self.interval = interval
        self._start = clock()
        self._start_offset = start_offset
        self._last_time: float | None = None
        self._last_emitted = start_offset
        self._width = 0

    def _line(
        self,
        emitted: int,
        total: int | None,
        retries: int,
        lag: float,
        dead_lettered: int,
        rate: float,
        eta: float | None,
    ) -> str:
        text = f"emitted {emitted:,}"
        if total is not None:
            text += f" / {total:,}"
        text += f" events  {rate:,.0f}/s  lag {lag:.1f}s  retries {retries}"
        text += f"  dead-lettered {dead_lettered}"
        if eta is not None:
            text += f"  eta {format_eta(eta)}"
        return text

    def _write(self, line: str, *, final: bool) -> None:
        if self.tty:
            padded = line.ljust(self._width)
            self._width = max(self._width, len(line))
            self.stream.write("\r" + padded + ("\n" if final else ""))
        else:
            self.stream.write(line + "\n")
        self.stream.flush()

    def update(
        self,
        emitted: int,
        total: int | None,
        *,
        retries: int,
        lag: float,
        dead_lettered: int,
        rate: float | None = None,
    ) -> None:
        """Write the line if a second has passed since the last one (the first call writes at
        once). ``rate`` is measured since the last write when not given."""
        now = self._clock()
        if self._last_time is not None and now - self._last_time < self.interval:
            return
        since = now - (self._start if self._last_time is None else self._last_time)
        if rate is None:
            rate = (emitted - self._last_emitted) / since if since > 0 else 0.0
        eta = None
        if total is not None and rate > 0 and emitted < total:
            eta = (total - emitted) / rate
        self._write(self._line(emitted, total, retries, lag, dead_lettered, rate, eta), final=False)
        self._last_time, self._last_emitted = now, emitted

    def finish(
        self, emitted: int, total: int | None, *, retries: int, lag: float, dead_lettered: int
    ) -> None:
        """The final line: the rate over the whole run, no ETA, and a newline."""
        elapsed = self._clock() - self._start
        rate = (emitted - self._start_offset) / elapsed if elapsed > 0 else 0.0
        self._write(self._line(emitted, total, retries, lag, dead_lettered, rate, None), final=True)
