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


def parse_speed(text: str | float) -> float:
    """``60x``, ``60`` or ``0.5x`` as a speed factor: event time passes that many times faster
    than the wall clock."""
    raw = str(text).strip().lower().removesuffix("x")
    try:
        speed = float(raw)
    except ValueError:
        raise ValueError(f"speed {text!r}: expected a number such as 60x") from None
    if not math.isfinite(speed) or speed <= 0:
        raise ValueError(f"speed {text!r}: must be a positive number")
    return speed


class VirtualClock:
    """Pace a stream by its *event time* instead of a rate: ``speed`` is how many times faster
    than the wall clock the event time passes (``60`` replays an hour in a minute).

    ``due(event_time)`` is the wall-clock second (after the start of the run) at which an event
    stamped ``event_time`` is due: ``(event_time - first_event_time) / speed``. The first event
    seen is the origin, so a run resumed from a checkpoint starts at once and keeps the spacing
    of the events. The result never goes backwards: an event that is earlier than one already
    sent (out-of-order delivery) is due at once, and the clock waits for the next later one.
    """

    def __init__(self, speed: float) -> None:
        if not math.isfinite(speed) or speed <= 0:
            raise ValueError("speed must be a positive number")
        self.speed = float(speed)
        self._origin: float | None = None
        self._latest = 0.0
        self.span = 0.0  # event-time seconds replayed so far

    def due(self, event_time: float | None) -> float:
        """The due time of an event at ``event_time`` seconds (any epoch); ``None`` (no time) is
        due when the previous event was."""
        if event_time is None:
            return self._latest
        if self._origin is None:
            self._origin = event_time
        offset = (event_time - self._origin) / self.speed
        if offset > self._latest:
            self._latest = offset
            self.span = max(self.span, event_time - self._origin)
        return self._latest


class RateCap:
    """A hard ceiling on delivery: at most ``cap`` events per second over the run, however fast
    the schedule or the sink would go. ``due(n)`` is when ``n`` events may have been sent."""

    def __init__(self, cap: float) -> None:
        if not math.isfinite(cap) or cap <= 0:
            raise ValueError("the rate cap must be a positive number of events per second")
        self.cap = float(cap)

    def due(self, n: float) -> float:
        return max(0.0, n) / self.cap
