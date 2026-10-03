"""Realtime pacing: a constant rate with optional bursts (P5-01).

``RateSchedule`` answers one question: at what time (seconds after the run started) is the n-th
event due? The runtime sleeps until that absolute time instead of sleeping a fixed gap per batch,
so sleep overshoot never accumulates and the long-run rate equals the target.

A burst is ``START:DURATION:MULT``: from ``START`` seconds for ``DURATION`` seconds the rate is
``MULT`` times the base rate (``MULT`` below 1 is a slow-down). Bursts may not overlap.

Arrival processes (W2-09): ``constant`` (the default) spaces events evenly at the current rate.
``poisson`` draws the gaps from an exponential distribution: in *operational time* (the integral
of the rate) the arrivals are a unit-rate Poisson process whose gaps ``E_i`` are exponential with
mean 1, and event ``n`` is due when the integral of the rate reaches ``E_0 + ... + E_{n-1}``.
Bursts (and ramps, curves) change the rate and the process follows. The draw for position ``p`` is
a function of the seed and ``p`` alone, so the schedule is the same on every run, and a run
resumed at offset ``k`` (``resume_at``) sees the gaps the uninterrupted run had from ``k`` on.
"""

from __future__ import annotations

import bisect
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np


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


ARRIVALS = ("constant", "poisson")
_BLOCK = 1 << 16  # gaps are drawn, and summed, a block at a time


class RateSchedule:
    """The due time of every event for a base ``rate`` (events per second) and ``bursts``.

    ``arrivals`` is ``constant`` (even spacing) or ``poisson`` (exponential gaps keyed by ``seed``
    and the event position); see the module docstring."""

    def __init__(
        self,
        rate: float,
        bursts: Sequence[Burst] = (),
        *,
        arrivals: str = "constant",
        seed: int = 0,
    ) -> None:
        if not math.isfinite(rate) or rate <= 0:
            raise ValueError("rate must be a positive number of events per second")
        if arrivals not in ARRIVALS:
            raise ValueError(f"arrivals must be one of {', '.join(ARRIVALS)}, got {arrivals!r}")
        self.arrivals = arrivals
        self.seed = int(seed)
        self._base = 0  # the event position the schedule starts at (a resumed run)
        self._base_tau = 0.0
        self._prefix = [0.0]  # sum of the gaps of every block before block i
        self._cached: tuple[int, np.ndarray[Any, Any]] | None = None
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

    # ---- the Poisson draws --------------------------------------------------------------

    def _block(self, b: int) -> np.ndarray[Any, Any]:
        """Cumulative gaps within block ``b``: ``out[i]`` is the sum of the first ``i + 1``."""
        if self._cached is not None and self._cached[0] == b:
            return self._cached[1]
        from shape.streaming.emit.faults import event_uniform

        position = np.arange(b * _BLOCK, (b + 1) * _BLOCK, dtype=np.int64)
        u = event_uniform(self.seed, "arrivals", "_arrivals", position)
        cumulative = np.cumsum(-np.log1p(-u))
        self._cached = (b, cumulative)
        return cumulative

    def _tau(self, n: int) -> float:
        """The sum of the gaps of events ``0 .. n - 1``: operational time at event ``n``."""
        b, r = divmod(int(n), _BLOCK)
        while len(self._prefix) <= b:
            last = len(self._prefix) - 1
            self._prefix.append(self._prefix[last] + float(self._block(last)[-1]))
        return self._prefix[b] + (float(self._block(b)[r - 1]) if r else 0.0)

    def resume_at(self, position: int) -> None:
        """Start the schedule at event ``position`` (the offset a run resumes from): its gaps are
        the ones the uninterrupted run had from there on. Does nothing for ``constant``."""
        if position < 0:
            raise ValueError("position must be 0 or more")
        self._base = int(position)
        self._base_tau = self._tau(self._base) if self.arrivals == "poisson" else 0.0

    # ---- the schedule -------------------------------------------------------------------

    def _operational(self, n: float) -> float:
        if self.arrivals == "constant":
            return n
        return self._tau(self._base + int(n)) - self._base_tau

    def due_time(self, n: float) -> float:
        """Seconds after the start at which ``n`` events have been due (``n`` of them before it)."""
        if n <= 0:
            return 0.0
        x = self._operational(n)
        i = bisect.bisect_right(self._cum, x) - 1
        return self._t0[i] + (x - self._cum[i]) / self._rate[i]

    def expected_by(self, t: float) -> float:
        """The integral of the rate over ``[0, t]``: the events expected by ``t`` seconds after
        the start (what ``constant`` delivers, and the mean of ``poisson``)."""
        if t <= 0:
            return 0.0
        i = bisect.bisect_right(self._t0, t) - 1
        return self._cum[i] + (t - self._t0[i]) * self._rate[i]

    def events_by(self, t: float) -> float:
        """The number of events due by ``t`` seconds after the start: for ``constant`` the
        expectation, for ``poisson`` the realised count."""
        x = self.expected_by(t)
        if self.arrivals == "constant" or x <= 0:
            return x
        # the events whose operational time is at most x: events 0 .. k - 1 with gaps summing to x
        lo, hi = 0, 1
        while self._operational(hi) <= x:
            lo, hi = hi, hi * 2
        while hi - lo > 1:
            mid = (lo + hi) // 2
            lo, hi = (mid, hi) if self._operational(mid) <= x else (lo, mid)
        return float(lo + 1)


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
