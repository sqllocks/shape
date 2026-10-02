"""Realtime pacing: a constant rate with optional bursts (P5-01).

``RateSchedule`` answers one question: at what time (seconds after the run started) is the n-th
event due? The runtime sleeps until that absolute time instead of sleeping a fixed gap per batch,
so sleep overshoot never accumulates and the long-run rate equals the target.

A burst is ``START:DURATION:MULT``: from ``START`` seconds for ``DURATION`` seconds the rate is
``MULT`` times the base rate (``MULT`` below 1 is a slow-down). Bursts may not overlap.
"""

from __future__ import annotations

import bisect
import math
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Burst:
    start: float
    duration: float
    mult: float

    @property
    def end(self) -> float:
        return self.start + self.duration


def parse_burst(spec: str) -> Burst:
    """``START:DURATION:MULT`` (seconds, seconds, factor) to a :class:`Burst`."""
    parts = spec.split(":")
    if len(parts) != 3:
        raise ValueError(f"burst {spec!r}: expected START:DURATION:MULT")
    try:
        start, duration, mult = (float(p) for p in parts)
    except ValueError:
        raise ValueError(f"burst {spec!r}: START, DURATION and MULT must be numbers") from None
    if not all(math.isfinite(v) for v in (start, duration, mult)):
        raise ValueError(f"burst {spec!r}: START, DURATION and MULT must be finite")
    if start < 0:
        raise ValueError(f"burst {spec!r}: START must be 0 or more")
    if duration <= 0:
        raise ValueError(f"burst {spec!r}: DURATION must be positive")
    if mult <= 0:
        raise ValueError(f"burst {spec!r}: MULT must be positive")
    return Burst(start, duration, mult)


class RateSchedule:
    """The due time of every event for a base ``rate`` (events per second) and ``bursts``."""

    def __init__(self, rate: float, bursts: Sequence[Burst] = ()) -> None:
        if not math.isfinite(rate) or rate <= 0:
            raise ValueError("rate must be a positive number of events per second")
        ordered = sorted(bursts, key=lambda b: b.start)
        for before, after in zip(ordered, ordered[1:], strict=False):
            if after.start < before.end:
                raise ValueError("bursts may not overlap")
        self.rate = float(rate)
        self.bursts = tuple(ordered)
        # Segments (t0, rate, events before t0); the last one has no end.
        t0s: list[float] = []
        rates: list[float] = []
        cums: list[float] = []
        t, cum = 0.0, 0.0

        def add(until: float | None, r: float) -> None:
            nonlocal t, cum
            t0s.append(t)
            rates.append(r)
            cums.append(cum)
            if until is not None:
                cum += (until - t) * r
                t = until

        for b in ordered:
            if b.start > t:
                add(b.start, self.rate)
            add(b.end, self.rate * b.mult)
        add(None, self.rate)
        self._t0, self._rate, self._cum = t0s, rates, cums

    def due_time(self, n: float) -> float:
        """Seconds after the start at which ``n`` events have been due (``n`` of them before it)."""
        if n <= 0:
            return 0.0
        i = bisect.bisect_right(self._cum, n) - 1
        return self._t0[i] + (n - self._cum[i]) / self._rate[i]

    def events_by(self, t: float) -> float:
        """The number of events due by ``t`` seconds after the start."""
        if t <= 0:
            return 0.0
        i = bisect.bisect_right(self._t0, t) - 1
        return self._cum[i] + (t - self._t0[i]) * self._rate[i]
