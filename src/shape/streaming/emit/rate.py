"""Realtime pacing: a rate with optional bursts, ramps and a daily curve (P5-01, W2-09).

``RateSchedule`` answers one question: at what time (seconds after the run started) is the n-th
event due? The runtime sleeps until that absolute time instead of sleeping a fixed gap per batch,
so sleep overshoot never accumulates and the long-run rate equals the target.

A burst is ``START:DURATION:MULT``: from ``START`` seconds for ``DURATION`` seconds the rate is
``MULT`` times the base rate (``MULT`` below 1 is a slow-down). Bursts may not overlap.

Rate multipliers (W2-09) multiply the base rate, and each other:

* a **burst** (``START:DURATION:MULT``) is a step: ``MULT`` times the rate inside its window;
* a **ramp** (``START:DURATION:FROM:TO``) moves the multiplier linearly from ``FROM`` to ``TO``
  over its window. Before the first ramp the multiplier is 1; after a ramp it holds that ramp's
  ``TO`` until the next ramp begins (so two ramps with a gap between them make a plateau). Ramps
  may not overlap each other;
* a **daily curve** (:class:`DailyCurve`) is a 24-hour multiplier, linear between its points and
  wrapping at midnight. It follows the wall clock of the run (``day_origin`` is the time of day,
  in seconds after midnight, at the start of the run).

The schedule is absolute-time: the rate is a function of the seconds since the run started, so the
events delivered over a window equal the integral of the rate over it (to within one event), and a
slow sink never shifts the shape. With ``--speed`` the curve follows the *event time* instead
(see :class:`VirtualClock`).

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
import json
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from shape.errors import ShapeError


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


DAY = 86400.0


@dataclass(frozen=True, slots=True)
class Ramp:
    start: float
    duration: float
    from_mult: float
    to_mult: float

    @property
    def end(self) -> float:
        return self.start + self.duration


def parse_ramp(spec: str) -> Ramp:
    """``START:DURATION:FROM:TO`` (seconds, seconds, multiplier, multiplier) to a :class:`Ramp`."""
    parts = spec.split(":")
    if len(parts) != 4:
        raise ValueError(f"ramp {spec!r}: expected START:DURATION:FROM:TO")
    try:
        start, duration, from_mult, to_mult = (float(p) for p in parts)
    except ValueError:
        raise ValueError(f"ramp {spec!r}: START, DURATION, FROM and TO must be numbers") from None
    if not all(math.isfinite(v) for v in (start, duration, from_mult, to_mult)):
        raise ValueError(f"ramp {spec!r}: START, DURATION, FROM and TO must be finite")
    if start < 0:
        raise ValueError(f"ramp {spec!r}: START must be 0 or more")
    if duration <= 0:
        raise ValueError(f"ramp {spec!r}: DURATION must be positive")
    if from_mult < 0 or to_mult < 0:
        raise ValueError(f"ramp {spec!r}: FROM and TO must be 0 or more")
    return Ramp(start, duration, from_mult, to_mult)


CURVE_FORMAT = "shape-rate-curve"
CURVE_VERSION = 1
_CLOCK = re.compile(r"^([0-9]{2}):([0-9]{2})(?::([0-9]{2}))?$")


def _clock_seconds(text: Any, where: str) -> float:
    m = _CLOCK.match(text) if isinstance(text, str) else None
    if m is None or int(m.group(1)) > 23 or int(m.group(2)) > 59 or int(m.group(3) or 0) > 59:
        raise ShapeError(f"{where}: {text!r} is not a time of day (HH:MM or HH:MM:SS, 00:00-23:59)")
    return int(m.group(1)) * 3600.0 + int(m.group(2)) * 60.0 + int(m.group(3) or 0)


class DailyCurve:
    """A 24-hour rate multiplier: ``points`` are ``(seconds after midnight, multiplier)``,
    interpolated linearly, and the last point continues to the first of the next day."""

    def __init__(self, points: Sequence[tuple[float, float]], name: str = "custom") -> None:
        if not points:
            raise ShapeError("a rate curve needs at least one point")
        pts = sorted((float(t), float(v)) for t, v in points)
        for (t0, _), (t1, _) in zip(pts, pts[1:], strict=False):
            if t0 == t1:
                raise ShapeError(f"a rate curve has the time {_hhmm(t0)} twice")
        for t, v in pts:
            if not 0.0 <= t < DAY:
                raise ShapeError("a rate curve's times are within one day")
            if not math.isfinite(v) or v < 0:
                raise ShapeError(f"the multiplier at {_hhmm(t)} must be a number of 0 or more")
        if all(v == 0 for _, v in pts):
            raise ShapeError("the curve's multipliers are all 0: no event would ever be due")
        self.name = name
        self.points = pts
        first_t, first_v = pts[0]
        last_t, last_v = pts[-1]
        # the value at midnight, between the last point and the first of the next day
        if first_t == 0.0:
            midnight = first_v
        else:
            gap = first_t + DAY - last_t
            midnight = last_v + (first_v - last_v) * (DAY - last_t) / gap
        knots = [(0.0, midnight), *(p for p in pts if p[0] > 0.0), (DAY, midnight)]
        self._t = [k[0] for k in knots]
        self._v = [k[1] for k in knots]
        self.min_value = min(self._v)
        self._w: list[float] | None = None  # integral of 1 / multiplier up to each knot

    def _piece(self, tod: float) -> int:
        return min(bisect.bisect_right(self._t, tod) - 1, len(self._t) - 2)

    def piece_at(self, tod: float) -> tuple[float, float, float]:
        """``(multiplier at tod, slope per second, time of day where this piece ends)``."""
        tod = tod % DAY
        k = self._piece(tod)
        t0, t1, v0, v1 = self._t[k], self._t[k + 1], self._v[k], self._v[k + 1]
        slope = (v1 - v0) / (t1 - t0)
        return v0 + slope * (tod - t0), slope, t1

    def value(self, tod: float) -> float:
        return self.piece_at(tod)[0]

    def mean(self) -> float:
        return (
            sum(
                (self._t[k + 1] - self._t[k]) * (self._v[k] + self._v[k + 1]) / 2.0
                for k in range(len(self._t) - 1)
            )
            / DAY
        )

    def _inverse_integrals(self) -> list[float]:
        if self._w is None:
            w = [0.0]
            for k in range(len(self._t) - 1):
                w.append(w[-1] + self._inverse_piece(k, self._t[k + 1] - self._t[k]))
            self._w = w
        return self._w

    def _inverse_piece(self, k: int, dt: float) -> float:
        """The integral of ``1 / multiplier`` over the first ``dt`` seconds of piece ``k``."""
        t0, t1, v0, v1 = self._t[k], self._t[k + 1], self._v[k], self._v[k + 1]
        slope = (v1 - v0) / (t1 - t0)
        if slope == 0.0:
            return dt / v0
        return math.log((v0 + slope * dt) / v0) / slope

    def inverse_integral(self, t: float) -> float:
        """The integral of ``1 / multiplier`` from time 0 to ``t`` (seconds, any epoch; a day's
        worth wraps): the wall time a replay takes, at speed 1, to cover ``t`` seconds of event
        time. Needs a curve without a zero multiplier."""
        w = self._inverse_integrals()
        days, tod = divmod(t, DAY)
        k = self._piece(tod)
        return days * w[-1] + w[k] + self._inverse_piece(k, tod - self._t[k])


def _hhmm(seconds: float) -> str:
    return f"{int(seconds // 3600):02d}:{int(seconds % 3600 // 60):02d}"


BUILTIN_CURVES: dict[str, DailyCurve] = {
    "flat": DailyCurve([(0.0, 1.0)], "flat"),
    # quiet nights (0.15), a climb from 07:00 to the full rate at 09:00, a working day at 1.0 until
    # 17:00, an evening decline to 0.3 at 19:00 and back to 0.15 at 22:00
    "business-hours": DailyCurve(
        [
            (0.0, 0.15),
            (7 * 3600.0, 0.15),
            (9 * 3600.0, 1.0),
            (17 * 3600.0, 1.0),
            (19 * 3600.0, 0.3),
            (22 * 3600.0, 0.15),
        ],
        "business-hours",
    ),
}


def load_curve(name_or_path: str) -> DailyCurve:
    """A built-in curve by name (``flat``, ``business-hours``), or a ``shape-rate-curve`` JSON
    file: ``{"format": "shape-rate-curve", "version": 1, "points": [["00:00", 0.2], ...]}``."""
    if name_or_path in BUILTIN_CURVES:
        return BUILTIN_CURVES[name_or_path]
    try:
        with open(name_or_path, encoding="utf-8") as f:
            doc = json.load(f)
    except FileNotFoundError:
        raise ShapeError(
            f"unknown rate curve {name_or_path!r}: the built-in curves are "
            f"{', '.join(BUILTIN_CURVES)}, or give the path of a {CURVE_FORMAT} file"
        ) from None
    except (OSError, UnicodeDecodeError) as exc:
        raise ShapeError(f"cannot read the rate curve {name_or_path}: {exc}") from exc
    except ValueError:
        raise ShapeError(f"the rate curve {name_or_path} is not valid JSON") from None
    return curve_from_dict(doc, name_or_path)


def curve_from_dict(doc: Any, where: str = "rate curve") -> DailyCurve:
    if not isinstance(doc, dict):
        raise ShapeError(f"{where}: a rate curve is a JSON object")
    if doc.get("format") != CURVE_FORMAT:
        raise ShapeError(f"{where}: not a {CURVE_FORMAT} file")
    version = doc.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ShapeError(f"{where}: the version must be an integer of 1 or more")
    if version > CURVE_VERSION:
        raise ShapeError(
            f"{where}: this curve is version {version}, which is newer than the version "
            f"{CURVE_VERSION} this Shape reads; upgrade Shape to read it"
        )
    unknown = sorted(set(doc) - {"format", "version", "points", "name", "description"})
    if unknown:
        raise ShapeError(f"{where}: unknown key {unknown[0]!r}")
    raw = doc.get("points")
    if not isinstance(raw, list):
        raise ShapeError(f"{where}: 'points' is a list of [time, multiplier] pairs")
    if not raw:
        raise ShapeError(f"{where}: a rate curve needs at least one point")
    points: list[tuple[float, float]] = []
    for item in raw:
        if not isinstance(item, list) or len(item) != 2:
            raise ShapeError(f"{where}: each point is [time, multiplier]")
        at = _clock_seconds(item[0], where)
        mult = item[1]
        if isinstance(mult, bool) or not isinstance(mult, (int, float)):
            raise ShapeError(f"{where}: the multiplier at {item[0]} must be a number")
        points.append((at, float(mult)))
    return DailyCurve(points, str(doc.get("name", where)))


ARRIVALS = ("constant", "poisson")
_BLOCK = 1 << 16  # gaps are drawn, and summed, a block at a time


class RateSchedule:
    """The due time of every event for a base ``rate`` (events per second), ``bursts``, ``ramps``
    and a daily ``curve`` (``day_origin`` is the time of day, in seconds after midnight, when the
    run starts).

    ``arrivals`` is ``constant`` (even spacing) or ``poisson`` (exponential gaps keyed by ``seed``
    and the event position); see the module docstring."""

    def __init__(
        self,
        rate: float,
        bursts: Sequence[Burst] = (),
        *,
        ramps: Sequence[Ramp] = (),
        curve: DailyCurve | None = None,
        day_origin: float = 0.0,
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
        ramp_order = sorted(ramps, key=lambda r: r.start)
        for r_before, r_after in zip(ramp_order, ramp_order[1:], strict=False):
            if r_after.start < r_before.end:
                raise ValueError("ramps may not overlap")
        self.rate = float(rate)
        self.bursts = tuple(ordered)
        self.ramps = tuple(ramp_order)
        self.curve = curve
        self.day_origin = float(day_origin) % DAY
        tail = (ramp_order[-1].to_mult if ramp_order else 1.0) * (
            curve.mean() if curve is not None else 1.0
        )
        if tail <= 0:
            raise ValueError(
                f"the rate is 0 from the end of the last ramp ({ramp_order[-1].end:g} s) on: "
                "the schedule never delivers the remaining events"
            )
        # Where the rate changes shape (a burst or ramp edge). The curve's own breakpoints repeat
        # every day and are found as the schedule is built.
        self._edges = sorted(
            {b.start for b in ordered}
            | {b.end for b in ordered}
            | {r.start for r in ramp_order}
            | {r.end for r in ramp_order}
        )
        # Segments, built as far as they are asked for. Each is a cubic in the seconds ``s`` into
        # it: events before it + k1 s + k2 s^2 + k3 s^3 (a burst, a ramp and a curve piece multiply
        # to at most a quadratic rate).
        self._t0: list[float] = []
        self._cum: list[float] = []
        self._cum_end: list[float] = []
        self._coef: list[tuple[float, float, float]] = []
        self._len: list[float] = []
        self._end_t = 0.0
        self._end_cum = 0.0

    def set_day_origin(self, seconds: float) -> None:
        """Set the time of day (seconds after midnight) at which the run starts; only before the
        schedule has been asked for a time."""
        if self._t0:
            raise ValueError("the day origin cannot change once the schedule is in use")
        self.day_origin = float(seconds) % DAY

    # ---- the segments -------------------------------------------------------------------

    def _extend(self) -> None:
        """Build the next segment, from where the last one ended."""
        t = self._end_t
        nxt = math.inf
        i = bisect.bisect_right(self._edges, t)
        if i < len(self._edges):
            nxt = self._edges[i]
        c0, c1 = 1.0, 0.0
        if self.curve is not None:
            tod = (self.day_origin + t) % DAY
            c0, c1, end_tod = self.curve.piece_at(tod)
            nxt = min(nxt, t + max(end_tod - tod, 1e-6))
        mult = 1.0
        for b in self.bursts:
            if b.start <= t < b.end:
                mult = b.mult
        r0, r1 = 1.0, 0.0
        for r in self.ramps:
            if t >= r.end:
                r0 = r.to_mult
            elif t >= r.start:
                r1 = (r.to_mult - r.from_mult) / r.duration
                r0 = r.from_mult + r1 * (t - r.start)
                break
            else:
                break
        base = mult * self.rate
        k1, k2, k3 = base * r0 * c0, base * (r0 * c1 + r1 * c0) / 2.0, base * r1 * c1 / 3.0
        length = nxt - t
        self._t0.append(t)
        self._cum.append(self._end_cum)
        self._coef.append((k1, k2, k3))
        self._len.append(length)
        if math.isinf(length):
            self._end_t = self._end_cum = math.inf
        else:
            self._end_t = nxt
            self._end_cum += k1 * length + k2 * length**2 + k3 * length**3
        self._cum_end.append(self._end_cum)

    def _at_time(self, t: float) -> int:
        """The index of the segment holding time ``t`` (built on demand)."""
        while self._end_t <= t:
            self._extend()
        return bisect.bisect_right(self._t0, t) - 1

    def _at_events(self, x: float) -> int:
        """The index of the first segment that has delivered ``x`` events by its end."""
        while self._end_cum < x:
            self._extend()
        return bisect.bisect_left(self._cum_end, x)

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
        i = self._at_events(x)
        k1, k2, k3 = self._coef[i]
        want = x - self._cum[i]
        if want <= 0:
            return self._t0[i]
        if k2 == 0.0 and k3 == 0.0:
            return self._t0[i] + want / k1
        return self._t0[i] + _solve_cubic(k1, k2, k3, want, self._len[i])

    def expected_by(self, t: float) -> float:
        """The integral of the rate over ``[0, t]``: the events expected by ``t`` seconds after
        the start (what ``constant`` delivers, and the mean of ``poisson``)."""
        if t <= 0:
            return 0.0
        i = self._at_time(t)
        k1, k2, k3 = self._coef[i]
        s = t - self._t0[i]
        return self._cum[i] + k1 * s + k2 * s * s + k3 * s**3

    def peak_rate(self, until: float) -> float:
        """The highest instantaneous rate (events per second) over ``[0, until]``."""
        if until <= 0:
            return 0.0
        self._at_time(until)  # build the segments that cover it
        peak = 0.0
        for t0, (k1, k2, k3), length in zip(self._t0, self._coef, self._len, strict=True):
            if t0 >= until:
                break
            span = min(length, until - t0)
            candidates = [0.0, span]
            if k3 != 0.0:
                s = -k2 / (3.0 * k3)  # where the rate (a quadratic) turns
                if 0.0 < s < span:
                    candidates.append(s)
            for s in candidates:
                peak = max(peak, k1 + 2.0 * k2 * s + 3.0 * k3 * s * s)
        return peak

    def rate_at(self, t: float) -> float:
        """The instantaneous rate (events per second) ``t`` seconds after the start."""
        i = self._at_time(max(t, 0.0))
        k1, k2, k3 = self._coef[i]
        s = max(t, 0.0) - self._t0[i]
        return k1 + 2.0 * k2 * s + 3.0 * k3 * s * s

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


class DaySchedule:
    """Each day of a drift plan takes ``day_seconds`` of wall time, and its events are spread
    evenly over it: day ``d`` starts ``d x day_seconds`` seconds after the start. ``day_events``
    is the number of events of each day. A day with no events still takes its time; after the
    last event the schedule is at the end of the plan.

    It answers the same questions as :class:`RateSchedule` (``due_time``, ``expected_by``,
    ``resume_at``); after a resume the clock starts again at the offset."""

    def __init__(self, day_events: Sequence[int], day_seconds: float) -> None:
        if not math.isfinite(day_seconds) or day_seconds <= 0:
            raise ValueError("day_seconds must be a positive number of seconds")
        if not day_events:
            raise ValueError("a day schedule needs at least one day")
        self.day_seconds = float(day_seconds)
        self.day_events = [int(n) for n in day_events]
        self._starts = [0]
        for n in self.day_events:
            self._starts.append(self._starts[-1] + n)
        self._base = 0
        self._base_time = 0.0

    @property
    def total_seconds(self) -> float:
        return len(self.day_events) * self.day_seconds

    def _absolute(self, n: float) -> float:
        """Seconds after the start of the plan at which event ``n`` is due."""
        if n <= 0:
            return 0.0
        if n >= self._starts[-1]:
            return self.total_seconds
        day = bisect.bisect_right(self._starts, n) - 1
        return (day + (n - self._starts[day]) / self.day_events[day]) * self.day_seconds

    def resume_at(self, position: int) -> None:
        if position < 0:
            raise ValueError("position must be 0 or more")
        self._base = int(position)
        self._base_time = self._absolute(position)

    def due_time(self, n: float) -> float:
        """Seconds after the start of this run at which ``n`` events have been due."""
        if n <= 0:
            return 0.0
        return self._absolute(self._base + n) - self._base_time

    def expected_by(self, t: float) -> float:
        """The events due by ``t`` seconds after the start of this run."""
        if t <= 0:
            return 0.0
        at = t + self._base_time
        if at >= self.total_seconds:
            return float(self._starts[-1] - self._base)
        day = int(at // self.day_seconds)
        within = (at - day * self.day_seconds) / self.day_seconds
        return self._starts[day] + within * self.day_events[day] - self._base

    events_by = expected_by

    def peak_rate(self, until: float | None = None) -> float:
        """The highest rate of any day (its events over ``day_seconds``)."""
        return max(self.day_events) / self.day_seconds


def _solve_cubic(k1: float, k2: float, k3: float, want: float, length: float) -> float:
    """The ``s`` in ``[0, length]`` with ``k1 s + k2 s^2 + k3 s^3 = want``; the function is
    non-decreasing there (a rate is never negative), so Newton's method inside a bracket finds
    it."""
    lo, hi = 0.0, length
    s = min(max(want / k1, lo), hi) if k1 > 0 else (lo + hi) / 2.0
    for _ in range(100):
        f = k1 * s + k2 * s * s + k3 * s**3 - want
        if abs(f) <= 1e-12 * max(1.0, want):
            return s
        if f > 0:
            hi = s
        else:
            lo = s
        slope = k1 + 2.0 * k2 * s + 3.0 * k3 * s * s
        step = s - f / slope if slope > 0 else (lo + hi) / 2.0
        s = step if lo < step < hi else (lo + hi) / 2.0
    return s


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

    With a daily ``curve`` the replay speed at an event is ``speed`` times the curve's multiplier
    at that event's time of day (UTC, for a time without a zone): a busy hour replays faster than
    a quiet night, so the events delivered per second of wall time follow the curve. The due time
    is the integral of ``1 / (speed x multiplier)`` over the event time, so it is still a
    function of the event time alone.
    """

    def __init__(self, speed: float, curve: DailyCurve | None = None) -> None:
        if not math.isfinite(speed) or speed <= 0:
            raise ValueError("speed must be a positive number")
        if curve is not None and curve.min_value <= 0:
            raise ShapeError(
                "a curve with a 0 multiplier cannot pace a --speed replay (the replay would "
                "wait forever); give every point a multiplier above 0"
            )
        self.speed = float(speed)
        self.curve = curve
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
        if self.curve is None:
            offset = (event_time - self._origin) / self.speed
        else:  # the replay runs ``multiplier`` times faster at the time of day of the event
            walk = self.curve.inverse_integral(event_time) - self.curve.inverse_integral(
                self._origin
            )
            offset = walk / self.speed
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
