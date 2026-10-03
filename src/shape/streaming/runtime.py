"""The stream runtime (P3-01): micro-batches go through the profile engine in bounded mode.

A *windowed profiler* takes Arrow record batches that carry an event time, assigns every row to
tumbling, sliding or session windows, profiles each window with the kernel's bounded-mode
``ProfileState`` (the T-14 sketches: memory does not grow with the number of events), and emits a
``WindowProfile`` when the watermark closes the window. The rules are in
``docs/specs/STREAMING_SEMANTICS.md``; in short:

* event time is the ``_shape_event_time`` column (``event_time=`` names another);
* the watermark is the largest event time seen, minus ``allowed_lateness``, and it advances at
  batch boundaries (a micro-batch is classified against the watermark it started with); when the
  batches say which partition they came from, it is the *smallest* of the partitions' largest
  event times (see ``max_partition_skew``), so partitions read at different speeds lose nothing;
* a window closes when the watermark reaches its end; a row is *late* when every window it
  belongs to has already closed. Late rows are counted and either dropped or handed to
  ``late_sink``;
* ``snapshot()`` returns a JSON-safe dict and ``restore()`` rebuilds the profiler from it: a
  profiler killed mid-stream and restored from its last snapshot gives the output of an
  uninterrupted run, exactly.

Windows are aligned to the epoch (plus ``offset``). A sliding window of ``size`` every ``slide``
is assembled from panes of ``gcd(size, slide)`` that are profiled once and merged when a window
closes; a pane is dropped when the last window containing it has closed.
"""

from __future__ import annotations

import base64
import math
import re
import zlib
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]

from shape.kernel.dispatch import get_kernel
from shape.profile.engine import table_entry
from shape.streaming import versions
from shape.streaming.messages import partition_of

SNAPSHOT_FORMAT = "shape-stream-window-v1"
# How far (in event time) the slowest partition may trail the newest event before it stops holding
# the watermark back: ten minutes, in microseconds. See ``WindowedProfiler.max_partition_skew``.
DEFAULT_MAX_PARTITION_SKEW = 600_000_000
EVENT_TIME = "_shape_event_time"
_US = timedelta(microseconds=1)
_DAY_US = 86_400_000_000

LateSink = Callable[[pa.RecordBatch], None]
Duration = timedelta | str
"""A window size, slide, gap, offset or lateness: a ``timedelta``, or a string with a unit
(``"500ms"``, ``"60s"``, ``"5m"``, ``"1h"``, ``"2d"``, ``"250us"``; a bare numeric string is
seconds, as on the command line). A bare ``int`` is refused, except ``0``, which is the same in
every unit: it used to mean microseconds, so ``60_000`` was 60 *milliseconds*."""

_UNIT_US = {"us": 1, "ms": 1_000, "s": 1_000_000, "m": 60_000_000, "h": 3_600_000_000}
_UNIT_US["d"] = 24 * _UNIT_US["h"]
_DURATION = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(us|ms|s|m|h|d)?\s*$")


def parse_duration(text: str, what: str) -> int:
    """``"500ms"``, ``"30s"``, ``"5m"``, ``"1h"``, ``"2d"`` or a bare number of seconds, as
    whole microseconds."""
    m = _DURATION.match(text)
    if m is None:
        raise ValueError(f"{what}: {text!r} is not a duration (examples: 500ms, 30s, 5m, 1h)")
    us = round(float(m.group(1)) * _UNIT_US[m.group(2) or "s"])
    if not math.isfinite(us):
        raise ValueError(f"{what}: {text!r} is not a duration")
    return int(us)


def _micros(value: Duration, what: str, *, positive: bool) -> int:
    """A duration as whole microseconds."""
    if isinstance(value, timedelta):
        us = value // _US
    elif isinstance(value, str):
        us = parse_duration(value, what)
    elif isinstance(value, int) and not isinstance(value, bool) and value == 0:
        us = 0
    else:
        hint = (
            f"{what}: a bare number is not accepted, because its unit would be a guess "
            "(it used to be microseconds, so 60_000 was 60 ms): pass timedelta(seconds=60) or a "
            "string such as '60s' or '5m' (timedelta(microseconds=n) for microseconds)"
            if isinstance(value, int | float) and not isinstance(value, bool)
            else f"{what}: {value!r} is not a duration (a timedelta, or a string such as '60s')"
        )
        raise ValueError(hint)
    if us < 0 or (positive and us == 0):
        raise ValueError(f"{what} must be {'positive' if positive else 'zero or more'}")
    return us


def _stored(us: int) -> timedelta:
    """Whole microseconds (a snapshot holds them) as the ``timedelta`` the constructors take."""
    return timedelta(microseconds=int(us))


_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_MIN_US = (datetime.min.replace(tzinfo=UTC) - _EPOCH) // _US
_MAX_US = (datetime.max.replace(tzinfo=UTC) - _EPOCH) // _US


def _to_iso(us: int | None) -> str | None:
    if us is None:
        return None
    return _datetime(us).isoformat()


@dataclass(frozen=True)
class WindowProfile:
    """The profile of one closed window.

    ``start`` and ``end`` are microseconds since the epoch (``end`` is exclusive); a session
    window ends one gap after its last event, and the global window has neither. ``profile`` is
    the table entry the profile engine returns (``{name, rows, columns}``).
    """

    kind: str
    start: int | None
    end: int | None
    rows: int
    profile: dict[str, Any]

    @property
    def start_time(self) -> datetime | None:
        return None if self.start is None else _datetime(self.start)

    @property
    def end_time(self) -> datetime | None:
        return None if self.end is None else _datetime(self.end)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "start": _to_iso(self.start),
            "end": _to_iso(self.end),
            "start_us": self.start,
            "end_us": self.end,
            "rows": self.rows,
            "profile": self.profile,
        }


def _datetime(us: int) -> datetime:
    """``us`` microseconds since the epoch as a datetime; an instant outside the calendar
    (years 1 to 9999: a window around ``9999-12-31`` ends after it) is clamped to its edge. The
    integer bounds of a window stay exact."""
    return _EPOCH + timedelta(microseconds=min(max(us, _MIN_US), _MAX_US))


def event_times(array: pa.Array) -> tuple[np.ndarray, np.ndarray]:
    """Event times as int64 microseconds since the epoch, and a validity mask.

    Timestamps are read as instants (the zone is ignored), dates as midnight UTC. Null event
    times are marked invalid; their value is 0.
    """
    t = array.type
    if pa.types.is_timestamp(t):
        us = pc.cast(array, pa.timestamp("us"), safe=False)
    elif pa.types.is_date32(t):
        days = pc.cast(pc.cast(array, pa.int32()), pa.int64())
        us = pc.multiply(days, pa.scalar(_DAY_US, pa.int64()))
    elif pa.types.is_date64(t):
        us = pc.multiply(pc.cast(array, pa.int64()), pa.scalar(1000, pa.int64()))
    else:
        raise TypeError(f"event time must be a timestamp or a date column, not {t}")
    valid = np.asarray(array.is_valid())
    values = us.cast(pa.int64()).fill_null(0).to_numpy(zero_copy_only=False)
    return values.astype(np.int64, copy=False), valid


def _encode_state(state: Any) -> str:
    return base64.b64encode(zlib.compress(state.snapshot(), 6)).decode("ascii")


def _decode_state(schema: pa.Schema, text: str) -> Any:
    raw = zlib.decompress(base64.b64decode(text))
    return get_kernel().ProfileState.from_snapshot(schema, raw)


def _encode_schema(schema: pa.Schema) -> str:
    return base64.b64encode(schema.serialize().to_pybytes()).decode("ascii")


def _decode_schema(text: str) -> pa.Schema:
    return pa.ipc.read_schema(pa.py_buffer(base64.b64decode(text)))


class WindowedProfiler:
    """Shared machinery: validation, watermark, late handling, counters, snapshots.

    Subclasses decide how rows map to windows (``_route``), when windows close (``_close``) and
    what their state is made of (``_dump_state`` / ``_load_state``).
    """

    kind = "window"

    def __init__(
        self,
        schema: pa.Schema,
        *,
        event_time: str | None = EVENT_TIME,
        allowed_lateness: Duration = timedelta(0),
        late_sink: LateSink | None = None,
        name: str = "stream",
        top_n: int = 500,
    ) -> None:
        self.schema = pa.schema(schema).remove_metadata()
        self.event_time = event_time
        self.allowed_lateness = _micros(allowed_lateness, "allowed_lateness", positive=False)
        self.late_sink = late_sink
        self.name = name
        self.top_n = int(top_n)
        if self.top_n < 1:
            raise ValueError("top_n must be positive")
        self._time_index: int | None = None
        if event_time is not None:
            self._time_index = self.schema.get_field_index(event_time)
            if self._time_index < 0:
                raise ValueError(f"the schema has no event-time column {event_time!r}")
            t = self.schema.field(self._time_index).type
            if not (pa.types.is_timestamp(t) or pa.types.is_date32(t) or pa.types.is_date64(t)):
                raise TypeError(f"event-time column {event_time!r} must be a timestamp or a date")
        self._max_event_time: int | None = None
        # Per-partition watermarks (issue #41). Empty until a partition is registered; then the
        # watermark is the smallest newest-event time over the partitions that are not idle.
        self.max_partition_skew = DEFAULT_MAX_PARTITION_SKEW
        self._partitions: dict[str, int | None] = {}  # partition -> its newest event time
        self._idle: set[str] = set()
        self._wm_peak: int | None = None  # the watermark never moves back
        self.max_late_lag = 0  # microseconds: the furthest behind the watermark a late row was
        self.batches = 0
        self.rows_in = 0
        self.late_events = 0
        self.null_event_time = 0
        self.windows_emitted = 0
        self._finished = False

    # ---------------------------------------------------------------- state
    @property
    def watermark(self) -> int | None:
        """Microseconds since the epoch, or ``None`` before the first event."""
        if self._partitions:
            return self._wm_peak
        if self._max_event_time is None:
            return None
        return self._max_event_time - self.allowed_lateness

    @property
    def partitions(self) -> dict[str, int | None]:
        """Each known partition's newest event time in microseconds (``None``: none yet)."""
        return dict(self._partitions)

    def register_partitions(self, partitions: Iterable[str]) -> None:
        """Name the partitions of the stream. From then on the watermark waits for every one of
        them that is not idle (``max_partition_skew`` bounds the wait), so a partition that has
        not delivered yet does not see its first events called late."""
        for p in partitions:
            self._partitions.setdefault(str(p), None)

    def set_idle(self, partition: str, idle: bool = True) -> None:
        """Take a partition out of the watermark (it has nothing to say for now) or put it back.
        A partition that delivers again is active again."""
        if partition in self._partitions:
            (self._idle.add if idle else self._idle.discard)(partition)

    def _partition_watermark(self) -> int | None:
        newest = self._max_event_time
        if newest is None:
            return None
        floor = newest - self.max_partition_skew  # a partition further behind than this is ignored
        lows = [
            floor if seen is None else max(seen, floor)
            for p, seen in self._partitions.items()
            if p not in self._idle
        ]
        return (min(lows) if lows else newest) - self.allowed_lateness

    @property
    def finished(self) -> bool:
        return self._finished

    def _new_state(self) -> Any:
        return get_kernel().ProfileState(self.schema, "bounded")

    def _emit(self, start: int | None, end: int | None, state: Any) -> WindowProfile:
        self.windows_emitted += 1
        entry = table_entry(state, self.name, self.schema, "bounded", self.top_n)
        return WindowProfile(self.kind, start, end, int(state.rows), entry)

    # ----------------------------------------------------------- processing
    def process(
        self, batch: pa.RecordBatch | pa.Table, partition: str | None = None
    ) -> list[WindowProfile]:
        """Take one micro-batch; return the windows it caused to close, oldest first.

        ``partition`` names the partition the batch came from (``None``: the batch carries it in
        its schema metadata, as ``decode_messages`` writes it, or it is unknown). Once partitions
        are known the watermark is kept per partition."""
        if self._finished:
            raise RuntimeError("the stream has finished; no more batches can be processed")
        if isinstance(batch, pa.Table):
            out: list[WindowProfile] = []
            for part in batch.to_batches():
                out.extend(self.process(part, partition))
            return out
        if not batch.schema.equals(self.schema, check_metadata=False):
            raise ValueError("batch schema differs from the stream schema")
        self.batches += 1
        n = batch.num_rows
        self.rows_in += n
        if n == 0:
            return []
        ts, valid = self._times(batch)
        self.null_event_time += int(n - np.count_nonzero(valid))
        before = self.watermark
        late = self._route(batch, ts, valid, before)
        n_late = int(np.count_nonzero(late))
        if n_late:
            self.late_events += n_late
            if before is not None:
                self.max_late_lag = max(self.max_late_lag, int(before - ts[late].min()))
            if self.late_sink is not None:
                self.late_sink(batch.filter(pa.array(late)))
        if self._time_index is not None and valid.any():
            newest = int(ts[valid].max())
            if self._max_event_time is None or newest > self._max_event_time:
                self._max_event_time = newest
            if self._partitions:
                self._advance_partitions(
                    partition if partition is not None else partition_of(batch), newest
                )
        wm = self.watermark
        return [] if wm is None else self._close(wm)

    def _advance_partitions(self, partition: str | None, newest: int) -> None:
        """Record ``newest`` for the batch's partition (every partition, when it is not known)
        and move the watermark forward."""
        if partition is None:
            names = list(self._partitions)
        else:
            self._partitions.setdefault(partition, None)
            names = [partition]
        for p in names:
            seen = self._partitions[p]
            if seen is None or newest > seen:
                self._partitions[p] = newest
            self._idle.discard(p)
        candidate = self._partition_watermark()
        if candidate is not None and (self._wm_peak is None or candidate > self._wm_peak):
            self._wm_peak = candidate

    def finish(self) -> list[WindowProfile]:
        """End of stream: close every open window, oldest first."""
        if self._finished:
            return []
        out = self._close(None)
        self._finished = True
        return out

    def run(self, batches: Iterable[pa.RecordBatch | pa.Table]) -> Iterator[WindowProfile]:
        """Process every batch, then finish, yielding each window as it closes."""
        for batch in batches:
            yield from self.process(batch)
        yield from self.finish()

    def _times(self, batch: pa.RecordBatch) -> tuple[np.ndarray, np.ndarray]:
        if self._time_index is None:
            n = batch.num_rows
            return np.zeros(n, dtype=np.int64), np.ones(n, dtype=bool)
        return event_times(batch.column(self._time_index))

    # ------------------------------------------------------- for subclasses
    def _route(
        self, batch: pa.RecordBatch, ts: np.ndarray, valid: np.ndarray, wm: int | None
    ) -> np.ndarray:
        """Profile the rows into their windows; return the mask of late rows."""
        raise NotImplementedError

    def _close(self, wm: int | None) -> list[WindowProfile]:
        """Emit the windows that end at or before ``wm`` (all of them when ``wm`` is None)."""
        raise NotImplementedError

    def _config(self) -> dict[str, Any]:
        return {}

    def _dump_state(self) -> dict[str, Any]:
        raise NotImplementedError

    def _load_state(self, state: dict[str, Any]) -> None:
        raise NotImplementedError

    # ------------------------------------------------------------ snapshots
    def snapshot(self) -> dict[str, Any]:
        """Everything needed to resume: configuration, watermark, counters and window state."""
        return versions.stamp(
            versions.WINDOW_SNAPSHOT,
            {
                "kind": self.kind,
                "name": self.name,
                "schema": _encode_schema(self.schema),
                "event_time": self.event_time,
                "allowed_lateness_us": self.allowed_lateness,
                "top_n": self.top_n,
                "config": self._config(),
                "max_event_time_us": self._max_event_time,
                "partitions": {
                    "newest_us": self._partitions,
                    "idle": sorted(self._idle),
                    "watermark_us": self._wm_peak,
                    "max_skew_us": self.max_partition_skew,
                },
                "finished": self._finished,
                "counters": {
                    "batches": self.batches,
                    "rows_in": self.rows_in,
                    "late_events": self.late_events,
                    "max_late_lag_us": self.max_late_lag,
                    "null_event_time": self.null_event_time,
                    "windows_emitted": self.windows_emitted,
                },
                "state": self._dump_state(),
            },
        )

    @classmethod
    def restore(
        cls, snapshot: dict[str, Any], *, late_sink: LateSink | None = None
    ) -> WindowedProfiler:
        """Rebuild a profiler from ``snapshot()``. ``late_sink`` is code, so it is not stored."""
        if snapshot.get("format") != SNAPSHOT_FORMAT:
            raise ValueError("not a stream window snapshot")
        versions.check(versions.WINDOW_SNAPSHOT, snapshot, error=ValueError)
        kind = snapshot["kind"]
        target = _KINDS.get(kind)
        if target is None or not issubclass(target, cls):
            raise ValueError(f"cannot restore a {kind!r} snapshot as {cls.__name__}")
        obj = target._from_snapshot(snapshot, late_sink)
        counters = snapshot["counters"]
        obj._max_event_time = snapshot["max_event_time_us"]
        parts = snapshot.get("partitions")  # absent in a snapshot taken before partitions
        if parts is not None:
            obj._partitions = {str(k): v for k, v in parts["newest_us"].items()}
            obj._idle = set(parts["idle"])
            obj._wm_peak = parts["watermark_us"]
            obj.max_partition_skew = int(parts["max_skew_us"])
        obj.max_late_lag = int(counters.get("max_late_lag_us", 0))
        obj._finished = bool(snapshot["finished"])
        obj.batches = int(counters["batches"])
        obj.rows_in = int(counters["rows_in"])
        obj.late_events = int(counters["late_events"])
        obj.null_event_time = int(counters["null_event_time"])
        obj.windows_emitted = int(counters["windows_emitted"])
        obj._load_state(snapshot["state"])
        return obj

    @classmethod
    def _from_snapshot(
        cls, snapshot: dict[str, Any], late_sink: LateSink | None
    ) -> WindowedProfiler:
        raise NotImplementedError

    @staticmethod
    def _common(snapshot: dict[str, Any], late_sink: LateSink | None) -> dict[str, Any]:
        return {
            "event_time": snapshot["event_time"],
            "allowed_lateness": _stored(snapshot["allowed_lateness_us"]),
            "late_sink": late_sink,
            "name": snapshot["name"],
            "top_n": snapshot["top_n"],
        }


# ------------------------------------------------------------ pane windows


class SlidingProfiler(WindowedProfiler):
    """Windows of ``size`` that start every ``slide`` (``slide <= size``), aligned to the epoch
    plus ``offset``. ``TumblingProfiler`` is the case ``slide == size``."""

    kind = "sliding"

    def __init__(
        self,
        schema: pa.Schema,
        size: Duration,
        slide: Duration,
        *,
        offset: Duration = timedelta(0),
        event_time: str | None = EVENT_TIME,
        allowed_lateness: Duration = timedelta(0),
        late_sink: LateSink | None = None,
        name: str = "stream",
        top_n: int = 500,
    ) -> None:
        if event_time is None:
            raise ValueError("time windows need an event-time column")
        super().__init__(
            schema,
            event_time=event_time,
            allowed_lateness=allowed_lateness,
            late_sink=late_sink,
            name=name,
            top_n=top_n,
        )
        self.size = _micros(size, "size", positive=True)
        self.slide = _micros(slide, "slide", positive=True)
        if self.slide > self.size:
            raise ValueError("slide must not exceed size (rows would fall between windows)")
        self.offset = _micros(offset, "offset", positive=False)
        self.pane = math.gcd(self.size, self.slide)
        self._panes: dict[int, Any] = {}
        self._closed_to: int | None = None  # every window ending at or before this has closed

    # ------------------------------------------------------------- geometry
    def _last_end(self, pane_index: np.ndarray) -> np.ndarray:
        """End of the newest window that contains each pane."""
        start = pane_index * self.pane + self.offset
        return ((start - self.offset) // self.slide) * self.slide + self.offset + self.size

    def _windows_of(self, pane_index: int) -> range:
        """Indices ``k`` of the windows ``[k*slide+offset, +size)`` that contain a pane."""
        start = pane_index * self.pane  # relative to the offset
        return range((start - self.size) // self.slide + 1, start // self.slide + 1)

    def _window_bounds(self, k: int) -> tuple[int, int]:
        start = k * self.slide + self.offset
        return start, start + self.size

    # ----------------------------------------------------------- processing
    def _route(
        self, batch: pa.RecordBatch, ts: np.ndarray, valid: np.ndarray, wm: int | None
    ) -> np.ndarray:
        pane_index = (ts - self.offset) // self.pane
        late = np.zeros(len(ts), dtype=bool)
        if wm is not None:
            late = valid & (self._last_end(pane_index) <= wm)
        take = valid & ~late
        if not take.any():
            return late
        if take.all():
            self._add(batch, pane_index)
            return late
        keep = np.flatnonzero(take)
        self._add(batch.take(pa.array(keep)), pane_index[keep])
        return late

    def _add(self, batch: pa.RecordBatch, pane_index: np.ndarray) -> None:
        first = int(pane_index[0])
        if np.all(pane_index == first):
            self._pane(first).update(batch)
            return
        order = np.argsort(pane_index, kind="stable")  # arrival order within a pane
        sorted_panes = pane_index[order]
        cuts = np.flatnonzero(np.diff(sorted_panes)) + 1
        for rows in np.split(order, cuts):
            self._pane(int(pane_index[rows[0]])).update(batch.take(pa.array(rows)))

    def _pane(self, index: int) -> Any:
        state = self._panes.get(index)
        if state is None:
            state = self._panes[index] = self._new_state()
        return state

    def _close(self, wm: int | None) -> list[WindowProfile]:
        if not self._panes:
            return []
        ready: set[int] = set()
        for index in self._panes:
            for k in self._windows_of(index):
                end = self._window_bounds(k)[1]
                if (wm is None or end <= wm) and (self._closed_to is None or end > self._closed_to):
                    ready.add(k)
        out: list[WindowProfile] = []
        per_window = self.size // self.pane
        for k in sorted(ready):
            start, end = self._window_bounds(k)
            first = (start - self.offset) // self.pane
            members = [self._panes[i] for i in range(first, first + per_window) if i in self._panes]
            if len(members) == 1:
                state = members[0]  # one pane is the window: no merge, same as a batch profile
            else:
                state = self._new_state()
                for pane in members:
                    state.merge(pane)
            out.append(self._emit(start, end, state))
        if wm is None:
            self._panes.clear()
            return out
        self._closed_to = wm if self._closed_to is None else max(self._closed_to, wm)
        # the oldest window still open starts here; older panes are in no open window
        k_open = (wm - self.size - self.offset) // self.slide + 1
        oldest_open = k_open * self.slide + self.offset
        for index in [i for i in self._panes if (i + 1) * self.pane + self.offset <= oldest_open]:
            del self._panes[index]
        return out

    # ------------------------------------------------------------ snapshots
    def _config(self) -> dict[str, Any]:
        return {"size_us": self.size, "slide_us": self.slide, "offset_us": self.offset}

    def _dump_state(self) -> dict[str, Any]:
        return {
            "closed_to_us": self._closed_to,
            "panes": [[i, _encode_state(s)] for i, s in sorted(self._panes.items())],
        }

    def _load_state(self, state: dict[str, Any]) -> None:
        self._closed_to = state["closed_to_us"]
        self._panes = {int(i): _decode_state(self.schema, s) for i, s in state["panes"]}

    @classmethod
    def _from_snapshot(
        cls, snapshot: dict[str, Any], late_sink: LateSink | None
    ) -> WindowedProfiler:
        cfg = snapshot["config"]
        return cls(
            _decode_schema(snapshot["schema"]),
            _stored(cfg["size_us"]),
            _stored(cfg["slide_us"]),
            offset=_stored(cfg["offset_us"]),
            **cls._common(snapshot, late_sink),
        )


class TumblingProfiler(SlidingProfiler):
    """Fixed windows of ``size`` that do not overlap."""

    kind = "tumbling"

    def __init__(
        self,
        schema: pa.Schema,
        size: Duration,
        *,
        offset: Duration = timedelta(0),
        event_time: str | None = EVENT_TIME,
        allowed_lateness: Duration = timedelta(0),
        late_sink: LateSink | None = None,
        name: str = "stream",
        top_n: int = 500,
    ) -> None:
        super().__init__(
            schema,
            size,
            size,
            offset=offset,
            event_time=event_time,
            allowed_lateness=allowed_lateness,
            late_sink=late_sink,
            name=name,
            top_n=top_n,
        )

    def _config(self) -> dict[str, Any]:
        return {"size_us": self.size, "offset_us": self.offset}

    @classmethod
    def _from_snapshot(
        cls, snapshot: dict[str, Any], late_sink: LateSink | None
    ) -> WindowedProfiler:
        cfg = snapshot["config"]
        return cls(
            _decode_schema(snapshot["schema"]),
            _stored(cfg["size_us"]),
            offset=_stored(cfg["offset_us"]),
            **cls._common(snapshot, late_sink),
        )


# ----------------------------------------------------------------- sessions


@dataclass
class _Session:
    start: int
    end: int  # exclusive: the last event plus the gap
    state: Any


class SessionProfiler(WindowedProfiler):
    """Sessions: a window extends while events keep arriving less than ``gap`` apart, and closes
    when the watermark passes ``last event + gap``. Sessions that an event bridges are merged."""

    kind = "session"

    def __init__(
        self,
        schema: pa.Schema,
        gap: Duration,
        *,
        event_time: str | None = EVENT_TIME,
        allowed_lateness: Duration = timedelta(0),
        late_sink: LateSink | None = None,
        name: str = "stream",
        top_n: int = 500,
    ) -> None:
        if event_time is None:
            raise ValueError("session windows need an event-time column")
        super().__init__(
            schema,
            event_time=event_time,
            allowed_lateness=allowed_lateness,
            late_sink=late_sink,
            name=name,
            top_n=top_n,
        )
        self.gap = _micros(gap, "gap", positive=True)
        self._sessions: list[_Session] = []  # open sessions, ordered by start

    def _route(
        self, batch: pa.RecordBatch, ts: np.ndarray, valid: np.ndarray, wm: int | None
    ) -> np.ndarray:
        gap = self.gap
        late = np.zeros(len(ts), dtype=bool)
        if wm is not None:
            behind = valid & (ts + gap <= wm)
            if behind.any() and self._sessions:
                starts = np.array([s.start for s in self._sessions], dtype=np.int64)
                ends = np.array([s.end for s in self._sessions], dtype=np.int64)
                inside = (
                    (ts[:, None] < ends[None, :]) & (ts[:, None] + gap > starts[None, :])
                ).any(axis=1)
                behind &= ~inside
            late = behind
        rows = np.flatnonzero(valid & ~late)
        if rows.size == 0:
            return late
        order = rows[np.argsort(ts[rows], kind="stable")]
        sorted_ts = ts[order]
        cuts = np.flatnonzero(np.diff(sorted_ts) >= gap) + 1
        for run in np.split(order, cuts):
            first, last = int(ts[run[0]]), int(ts[run[-1]])
            self._add_run(batch, np.sort(run), first, last + gap)
        return late

    def _add_run(self, batch: pa.RecordBatch, rows: np.ndarray, start: int, end: int) -> None:
        sub = batch if rows.size == batch.num_rows else batch.take(pa.array(rows))
        hits = [s for s in self._sessions if s.start < end and start < s.end]
        if not hits:
            state = self._new_state()
            state.update(sub)
            self._sessions.append(_Session(start, end, state))
        else:
            head = hits[0]
            for other in hits[1:]:
                head.state.merge(other.state)
                self._sessions.remove(other)
            head.state.update(sub)
            head.start = min(head.start, start, *(s.start for s in hits))
            head.end = max(head.end, end, *(s.end for s in hits))
        self._sessions.sort(key=lambda s: (s.start, s.end))

    def _close(self, wm: int | None) -> list[WindowProfile]:
        done = [s for s in self._sessions if wm is None or s.end <= wm]
        if not done:
            return []
        self._sessions = [s for s in self._sessions if wm is not None and s.end > wm]
        return [self._emit(s.start, s.end, s.state) for s in done]

    def _config(self) -> dict[str, Any]:
        return {"gap_us": self.gap}

    def _dump_state(self) -> dict[str, Any]:
        return {"sessions": [[s.start, s.end, _encode_state(s.state)] for s in self._sessions]}

    def _load_state(self, state: dict[str, Any]) -> None:
        self._sessions = [
            _Session(int(a), int(b), _decode_state(self.schema, s)) for a, b, s in state["sessions"]
        ]

    @classmethod
    def _from_snapshot(
        cls, snapshot: dict[str, Any], late_sink: LateSink | None
    ) -> WindowedProfiler:
        return cls(
            _decode_schema(snapshot["schema"]),
            _stored(snapshot["config"]["gap_us"]),
            **cls._common(snapshot, late_sink),
        )


# ------------------------------------------------------------------- global


class GlobalProfiler(WindowedProfiler):
    """One window over the whole stream, emitted by ``finish()``. It needs no event time, so a
    replayed file profiles exactly as the batch engine would in bounded mode."""

    kind = "global"

    def __init__(
        self,
        schema: pa.Schema,
        *,
        late_sink: LateSink | None = None,
        name: str = "stream",
        top_n: int = 500,
    ) -> None:
        super().__init__(
            schema,
            event_time=None,
            allowed_lateness=timedelta(0),
            late_sink=late_sink,
            name=name,
            top_n=top_n,
        )
        self._state: Any = self._new_state()

    def _route(
        self, batch: pa.RecordBatch, ts: np.ndarray, valid: np.ndarray, wm: int | None
    ) -> np.ndarray:
        self._state.update(batch)
        return np.zeros(batch.num_rows, dtype=bool)

    def peek(self) -> WindowProfile:
        """The profile of everything seen so far, without closing the window: the stream goes on
        (``finish()`` still emits the final one). Used for live reading of a running stream."""
        entry = table_entry(self._state, self.name, self.schema, "bounded", self.top_n)
        return WindowProfile(self.kind, None, None, int(self._state.rows), entry)

    def _close(self, wm: int | None) -> list[WindowProfile]:
        return [self._emit(None, None, self._state)] if wm is None else []

    def _dump_state(self) -> dict[str, Any]:
        return {"state": _encode_state(self._state)}

    def _load_state(self, state: dict[str, Any]) -> None:
        self._state = _decode_state(self.schema, state["state"])

    @classmethod
    def _from_snapshot(
        cls, snapshot: dict[str, Any], late_sink: LateSink | None
    ) -> WindowedProfiler:
        return cls(
            _decode_schema(snapshot["schema"]),
            late_sink=late_sink,
            name=snapshot["name"],
            top_n=snapshot["top_n"],
        )


_KINDS: dict[str, type[WindowedProfiler]] = {
    "tumbling": TumblingProfiler,
    "sliding": SlidingProfiler,
    "session": SessionProfiler,
    "global": GlobalProfiler,
}


def restore_profiler(
    snapshot: dict[str, Any], *, late_sink: LateSink | None = None
) -> WindowedProfiler:
    """Restore whichever kind of profiler a snapshot holds."""
    return WindowedProfiler.restore(snapshot, late_sink=late_sink)
