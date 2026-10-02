"""Live fidelity (P5-03): the emitted stream, scored against its target as it goes.

::

    EventPlan --> EmitRunner --> TeeSink --> the real sink (delivery, checkpoint)
                                   |
                                   +--> LiveFidelity: per table, the stream profiler
                                        (``shape.streaming.runtime``, bounded mode) and the score
                                        accumulators, compared with a ``TargetShape``;
                                        alerts when the score drifts

``shape fidelity REFERENCE SYNTHETIC`` (``docs/FIDELITY.md``) scores finished tables. The live
score is the *same score* (it goes through ``compare.score_prepared``, so the arithmetic is the
one function) computed from what the stream has delivered so far, without keeping the events:

* **exact** where it can be: the null rate, the mean and the spread (running moments), the
  Kolmogorov-Smirnov statistic and the distinct count up to ``sample_cap`` values / ``key_cap``
  distinct values per column, the value counts of categorical columns up to ``key_cap``;
* **bounded** past those caps: the Kolmogorov-Smirnov statistic uses a uniform reservoir sample of
  ``sample_cap`` values, the distinct count comes from the stream profiler's HyperLogLog
  (about 0.8% error), and the value counts of a categorical column come from the profiler's
  space-saving table. A column that fell back on any of these is listed under ``approximate``.

The tee never changes what is delivered: events go to the real sink first, are counted only after
``send`` returned, and a failure inside the live side switches it off (an alert of kind
``live-error``) instead of failing the run.

Alerts (``docs/EMIT.md``) are edge-triggered: one alert when a condition becomes true, one
``recovered`` alert when it stops being true.

* ``score-low`` (error): a table's score is below ``min_table_score``, or the overall score is
  below ``min_score`` (the overall is judged once every table is, because a few tables' mean is not
  the overall).
* ``column-low`` (warning): a column's score is below ``min_column_score`` (off by default).
* ``score-drop`` (warning): a table's (or the overall) score fell ``drop`` points below the best it
  had reached.
* ``live-error`` (error): the live side failed and was switched off.

The alerts judge a table only once ``min_events`` of its events were seen and ``min_progress`` of
the target table's rows: a table that is half emitted scores lower than the finished one (distinct
counts grow with the rows), and that is not drift. The score itself is never gated: at any moment it
is ``shape fidelity`` of the target against the events delivered so far. Tables are streamed one
after the other, so the overall live score covers the tables that have started; after the last
event it is that of ``shape fidelity`` on all the events.
"""

from __future__ import annotations

import json
import math
import sys
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]

from shape.errors import ShapeError
from shape.generation.report import compare as cmp
from shape.streaming.emit.formats import FIELD_SEQ, FIELD_TABLE, FIELD_TIME
from shape.streaming.emit.sinks import EventSink
from shape.streaming.runtime import GlobalProfiler

ALERT_FORMAT = "shape-live-alert-v1"
REPORT_FORMAT = "shape-live-fidelity-v1"
_RESERVED = (FIELD_TABLE, FIELD_SEQ, FIELD_TIME)
_EMPTY_TEXT = pa.chunked_array([pa.array([], pa.string())])
KIND_SCORE_LOW = "score-low"
KIND_COLUMN_LOW = "column-low"
KIND_SCORE_DROP = "score-drop"
KIND_LIVE_ERROR = "live-error"
KIND_RECOVERED = "recovered"


@dataclass(frozen=True, slots=True)
class LiveConfig:
    """Settings of the live comparison; the defaults are the ``shape fidelity`` pass marks."""

    sample_cap: int = 100_000  # values kept per numeric column for the KS statistic
    key_cap: int = 250_000  # distinct values (or categories) tracked per column
    chunk_rows: int = 8192  # events gathered per table before they are counted
    interval_events: int = 50_000  # evaluate after this many events ...
    interval_seconds: float = 2.0  # ... and at least this long after the last evaluation
    min_events: int = 1000  # events of a table before the alerts judge it ...
    min_progress: float = 0.5  # ... and this share of the target table's rows
    min_score: float = cmp.DEFAULT_THRESHOLD
    min_table_score: float = cmp.DEFAULT_TABLE_THRESHOLD
    min_column_score: float | None = None
    drop: float = 5.0
    profile: bool = True  # feed the stream profiler (the live profile document)
    seed: int = 0

    def __post_init__(self) -> None:
        for name in ("sample_cap", "key_cap", "chunk_rows", "interval_events", "min_events"):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be at least 1")
        if self.interval_seconds < 0 or self.drop < 0:
            raise ValueError("interval_seconds and drop must be 0 or more")
        if not 0.0 <= self.min_progress <= 1.0:
            raise ValueError("min_progress must be between 0 and 1")


# ---- accumulators -----------------------------------------------------------------------------


class _Unique:
    """The distinct values of a column, exact up to ``cap`` of them."""

    def __init__(self, cap: int) -> None:
        self.cap = cap
        self.parts: list[pa.Array] = []
        self.buffered = 0
        self.merged: pa.Array | None = None
        self.overflow = False

    def add(self, values: pa.Array) -> None:
        if self.overflow or len(values) == 0:
            return
        part = pc.unique(values)
        self.parts.append(part)
        self.buffered += len(part)
        if self.buffered >= max(65_536, 0 if self.merged is None else len(self.merged)):
            self._merge()

    def _merge(self) -> None:
        arrays = ([] if self.merged is None else [self.merged]) + self.parts
        self.parts, self.buffered = [], 0
        if not arrays:
            return
        self.merged = pc.unique(pa.concat_arrays(arrays))
        if len(self.merged) > self.cap:
            self.overflow, self.merged = True, None

    def count(self) -> int | None:
        """The distinct count, or ``None`` once there were more than ``cap``."""
        if self.parts:
            self._merge()
        if self.overflow:
            return None
        return 0 if self.merged is None else len(self.merged)


class _Counts:
    """Value counts of a column, exact up to ``cap`` distinct values."""

    def __init__(self, cap: int) -> None:
        self.cap = cap
        self.values: list[pa.Array] = []
        self.counts: list[pa.Array] = []
        self.buffered = 0
        self.size = 0
        self.overflow = False

    def add(self, values: pa.Array, counts: pa.Array) -> None:
        if self.overflow or len(values) == 0:
            return
        self.values.append(values)
        self.counts.append(counts)
        self.buffered += len(values)
        if self.buffered >= max(65_536, self.size):
            self._merge()

    def _merge(self) -> None:
        if not self.values:
            return
        table = pa.table({"v": pa.concat_arrays(self.values), "c": pa.concat_arrays(self.counts)})
        grouped = table.group_by("v", use_threads=False).aggregate([("c", "sum")])
        self.values = [grouped.column("v").combine_chunks()]
        self.counts = [grouped.column("c_sum").combine_chunks()]
        self.buffered = 0
        self.size = len(self.values[0])
        if self.size > self.cap:
            self.overflow = True
            self.values, self.counts = [], []

    def result(self) -> tuple[pa.Array, np.ndarray[Any, Any]] | None:
        """``(values, counts)``, or ``None`` once there were more than ``cap`` distinct values."""
        self._merge()
        if self.overflow:
            return None
        if not self.values:
            return pa.array([], pa.string()), np.zeros(0)
        return self.values[0], np.asarray(self.counts[0].to_numpy(), dtype=np.float64)


class _Sample:
    """The values of a column: all of them up to ``cap``, then a uniform reservoir sample."""

    def __init__(self, cap: int, seed: int) -> None:
        self.cap = cap
        self.rng = np.random.default_rng(seed)
        self.parts: list[np.ndarray[Any, Any]] = []
        self.full: np.ndarray[Any, Any] | None = None
        self.seen = 0
        self._sorted: np.ndarray[Any, Any] | None = None

    @property
    def sampled(self) -> bool:
        return self.full is not None

    def add(self, x: np.ndarray[Any, Any]) -> None:
        if x.size == 0:
            return
        self._sorted = None
        n = int(x.size)
        if self.full is None:
            self.parts.append(x)
            self.seen += n
            if self.seen > self.cap:
                everything = np.concatenate(self.parts)
                self.parts = []
                self.full = everything[: self.cap].copy()
                self._replace(everything[self.cap :], self.cap)
            return
        self._replace(x, self.seen)
        self.seen += n

    def _replace(self, x: np.ndarray[Any, Any], start: int) -> None:
        # Algorithm R, vectorised: item i (0-based, in the whole stream) replaces a slot with
        # probability cap / (i + 1); within one call a later item wins a shared slot, as it would
        # if the items came one at a time.
        assert self.full is not None
        if x.size == 0:
            return
        index = start + np.arange(x.size)
        slot = (self.rng.random(x.size) * (index + 1)).astype(np.int64)
        keep = slot < self.cap
        self.full[slot[keep]] = x[keep]

    def sorted_values(self) -> np.ndarray[Any, Any]:
        if self._sorted is None:
            values = self.full if self.full is not None else np.concatenate(self.parts or [[]])
            self._sorted = np.sort(np.asarray(values))
        return self._sorted


@dataclass(slots=True)
class _Info:
    """What the profiler knows about a column (used past the caps)."""

    distinct: float | None = None
    top: list[Any] | None = None


class _NumericColumn:
    """A numeric or datetime column: running moments, a sample for KS, the distinct count."""

    def __init__(self, name: str, arrow_type: pa.DataType, cfg: LiveConfig, seed: int) -> None:
        self.name = name
        self.type = arrow_type
        self.kind = (
            cmp.DATETIME
            if pa.types.is_timestamp(arrow_type) or pa.types.is_date(arrow_type)
            else cmp.NUMERIC
        )
        self.n = 0
        self.count = 0
        self.mean = 0.0
        self.m2 = 0.0
        self.sample = _Sample(cfg.sample_cap, seed)
        self.unique = _Unique(cfg.key_cap)

    def update(self, col: pa.Array | pa.ChunkedArray) -> None:
        arr = cmp._decoded(cmp._chunked(col))
        self.n += len(arr)
        nn = cmp._non_null(arr)
        if len(nn) == 0:
            return
        t = arr.type
        if self.kind == cmp.DATETIME:
            numbers = cmp._datetime_ns(nn)
        elif pa.types.is_decimal(t):
            numbers = np.asarray(nn.cast(pa.float64()).to_numpy())
        else:
            numbers = np.asarray(nn.to_numpy())
        x = numbers.astype(np.float64, copy=False)
        m = int(x.size)
        mean_b = float(np.mean(x, dtype=np.float64))
        m2_b = float(np.sum((x - mean_b) ** 2))
        total = self.count + m
        delta = mean_b - self.mean
        self.mean += delta * m / total
        self.m2 += m2_b + delta * delta * self.count * m / total
        self.count = total
        self.sample.add(numbers)
        for chunk in nn.chunks:
            self.unique.add(chunk)

    def prepared(self, info: _Info) -> tuple[cmp._Prepared, bool]:
        distinct = self.unique.count()
        approximate = self.sample.sampled
        if distinct is None:
            approximate = True
            distinct = round(info.distinct) if info.distinct is not None else self.unique.cap
        std = math.sqrt(self.m2 / (self.count - 1)) if self.count >= 2 else float("nan")
        return (
            cmp._Prepared(
                self.kind,
                self.n,
                _EMPTY_TEXT,
                self.sample.sorted_values(),
                n_valid=self.count,
                distinct=int(distinct),
                mean=self.mean if self.count else 0.0,
                std=std,
                n_numbers=self.count,
                presorted=True,
            ),
            approximate,
        )


class _TextColumn:
    """A categorical column (text, booleans, binary, anything else): value counts. Text whose
    every value is a number, or 95% of whose values are ISO dates, is scored as numeric or
    datetime, as the comparator does."""

    def __init__(
        self, name: str, arrow_type: pa.DataType, cfg: LiveConfig, seed: int, *, infer: bool
    ) -> None:
        self.name = name
        self.type = arrow_type
        self.infer = infer
        self.kind = cmp.OTHER
        if (
            pa.types.is_boolean(arrow_type)
            or pa.types.is_null(arrow_type)
            or pa.types.is_binary(arrow_type)
            or pa.types.is_large_binary(arrow_type)
            or infer
        ):
            self.kind = cmp.STRING
        self.cfg = cfg
        self.rng = np.random.default_rng(seed)
        self.n = 0
        self.valid = 0
        self.counts = _Counts(cfg.key_cap)
        self.broken = False

    def update(self, col: pa.Array | pa.ChunkedArray) -> None:
        arr = cmp._decoded(cmp._chunked(col))
        self.n += len(arr)
        nn = cmp._non_null(arr)
        self.valid += len(nn)
        if len(nn) == 0 or self.broken:
            return
        for chunk in nn.chunks:
            try:
                vc = pc.value_counts(chunk)
                self.counts.add(vc.field("values"), vc.field("counts"))
            except (pa.ArrowInvalid, pa.ArrowNotImplementedError):
                self.broken = True
                return

    def prepared(self, info: _Info) -> tuple[cmp._Prepared, bool]:
        approximate = False
        got = self.counts.result()
        if got is None:
            approximate = True
            got = self._from_top(info)
        values, counts = got
        if self.broken or len(values) == 0:
            keys, weights = pa.array([], pa.large_string()), np.zeros(0)
        else:
            keys, weights = cmp.keys_from_counts(values, counts)
        distinct = len(values)
        if self.counts.overflow:
            distinct = round(info.distinct) if info.distinct is not None else self.cfg.key_cap
        base = cmp._Prepared(
            self.kind,
            self.n,
            _EMPTY_TEXT,
            n_valid=self.valid,
            distinct=int(distinct),
            key_counts=(keys, weights),
        )
        if self.infer and len(values) and not self.counts.overflow:
            inferred = self._inferred(values, counts)
            if inferred is not None:
                kind, numbers, mean, std, n_numbers = inferred
                base.kind, base.numbers = kind, numbers
                base.mean, base.std, base.n_numbers, base.presorted = mean, std, n_numbers, True
        return base, approximate

    def _from_top(self, info: _Info) -> tuple[pa.Array, np.ndarray[Any, Any]]:
        top = info.top or []
        try:
            values = pa.array([t[0] for t in top], self.type)
        except (pa.ArrowInvalid, pa.ArrowTypeError):
            return pa.array([], pa.string()), np.zeros(0)
        return values, np.asarray([t[1] for t in top], dtype=np.float64)

    def _inferred(
        self, values: pa.Array, counts: np.ndarray[Any, Any]
    ) -> tuple[str, np.ndarray[Any, Any], float, float, int] | None:
        if not (pa.types.is_string(values.type) or pa.types.is_large_string(values.type)):
            return None
        text = values.cast(pa.large_string())
        numbers = cmp._numeric_from_strings(pa.chunked_array([text]))
        kind = cmp.NUMERIC
        weights = counts
        if numbers is None:
            parsed = self._parse_dates(text)
            if parsed is None:
                return None
            ok = ~np.isnan(parsed)
            if counts[ok].sum() < cmp._DATE_LIKE_SHARE * counts.sum():
                return None
            kind = cmp.DATETIME
            numbers, weights = parsed[ok], counts[ok]
        return (kind, *self._weighted(np.asarray(numbers, dtype=np.float64), weights))

    @staticmethod
    def _parse_dates(text: pa.Array) -> np.ndarray[Any, Any] | None:
        """Epoch nanoseconds per distinct text (NaN where it is not a date)."""
        parsed: pa.Array | None
        try:
            parsed = text.cast(pa.timestamp("ns"))
        except (pa.ArrowInvalid, pa.ArrowNotImplementedError):
            parsed = None
            for fmt in cmp._ISO_FORMATS:
                one = pc.strptime(text, format=fmt, unit="ns", error_is_null=True)
                parsed = one if parsed is None else pc.coalesce(parsed, one)
        if parsed is None:
            return None
        values = np.asarray(parsed.cast(pa.int64()).to_numpy(zero_copy_only=False), dtype=object)
        out = np.full(len(values), np.nan)
        valid = np.asarray(pc.is_valid(parsed).to_numpy(zero_copy_only=False), dtype=bool)
        out[valid] = [float(v) for v in values[valid]]
        return out

    def _weighted(
        self, x: np.ndarray[Any, Any], weights: np.ndarray[Any, Any]
    ) -> tuple[np.ndarray[Any, Any], float, float, int]:
        """Mean, sample standard deviation and a sorted sample of ``x`` repeated by ``weights``."""
        total = float(weights.sum())
        n = int(round(total))
        mean = float((x * weights).sum() / total) if total else 0.0
        std = (
            math.sqrt(float((weights * (x - mean) ** 2).sum() / (total - 1)))
            if total > 1
            else float("nan")
        )
        cap = self.cfg.sample_cap
        if n <= cap:
            sample = np.repeat(x, weights.astype(np.int64))
        else:
            sample = self.rng.choice(x, size=cap, p=weights / total)
        return np.sort(sample), mean, std, n


def _column(name: str, arrow_type: pa.DataType, cfg: LiveConfig, seed: int) -> Any:
    t = arrow_type.value_type if pa.types.is_dictionary(arrow_type) else arrow_type
    if pa.types.is_boolean(t):
        return _TextColumn(name, t, cfg, seed, infer=False)
    if pa.types.is_integer(t) or pa.types.is_floating(t) or pa.types.is_decimal(t):
        return _NumericColumn(name, t, cfg, seed)
    if pa.types.is_timestamp(t) or pa.types.is_date(t):
        return _NumericColumn(name, t, cfg, seed)
    infer = pa.types.is_string(t) or pa.types.is_large_string(t)
    return _TextColumn(name, t, cfg, seed, infer=infer)


# ---- the target -------------------------------------------------------------------------------


@dataclass(slots=True)
class TargetShape:
    """What the stream is compared with: the reference tables, prepared once.

    ``from_tables`` takes the tables themselves (a reference dataset, or a domain generated at a
    reference seed). The score is the one ``shape fidelity`` gives, so the target must be data."""

    columns: dict[str, dict[str, cmp._Prepared]]
    rows: dict[str, int]
    issues: dict[str, tuple[str, ...]] = field(default_factory=dict)

    @classmethod
    def from_tables(
        cls, tables: Mapping[str, pa.Table], *, only: Iterable[str] | None = None
    ) -> TargetShape:
        wanted = None if only is None else set(only)
        columns: dict[str, dict[str, cmp._Prepared]] = {}
        rows: dict[str, int] = {}
        issues: dict[str, tuple[str, ...]] = {}
        for name in sorted(tables):
            if wanted is not None and name not in wanted:
                continue
            table = tables[name]
            rows[name] = table.num_rows
            problems = []
            if not table.column_names:
                problems.append("the reference table has no columns")
            if table.num_rows == 0:
                problems.append("the reference table has no rows")
            issues[name] = tuple(problems)
            prepared: dict[str, cmp._Prepared] = {}
            for col in table.column_names:
                p = cmp.prepare_column(table.column(col))
                p.n_valid = len(p.non_null)
                p.distinct = cmp._distinct(p)
                if p.numbers is not None:
                    p.mean, p.std = cmp._moments(p)
                    p.n_numbers = int(p.numbers.size)
                    p.numbers = np.sort(p.numbers)
                    p.presorted = True
                prepared[col] = p
            columns[name] = prepared
        return cls(columns, rows, issues)

    @property
    def tables(self) -> list[str]:
        return sorted(self.columns)


# ---- one table of the stream ------------------------------------------------------------------


class _LiveTable:
    def __init__(self, name: str, schema: pa.Schema, cfg: LiveConfig, seed: int) -> None:
        self.name = name
        self.schema = schema
        self.cfg = cfg
        self.rows = 0
        self.pending: list[pa.RecordBatch] = []
        self.pending_rows = 0
        self.columns = {
            f.name: _column(f.name, f.type, cfg, seed * 7919 + i) for i, f in enumerate(schema)
        }
        self.profiler = GlobalProfiler(schema, name=name) if cfg.profile else None
        self.dirty = False
        self.cached: dict[str, tuple[cmp._Prepared, bool]] = {}

    def add(self, batch: pa.RecordBatch) -> None:
        batch = batch.select(self.schema.names)
        self.pending.append(batch)
        self.pending_rows += batch.num_rows
        self.rows += batch.num_rows
        self.dirty = True
        if self.pending_rows >= self.cfg.chunk_rows:
            self.flush()

    def flush(self) -> None:
        if not self.pending:
            return
        table = pa.Table.from_batches(self.pending, schema=self.schema)
        self.pending, self.pending_rows = [], 0
        for name, column in self.columns.items():
            column.update(table.column(name))
        if self.profiler is not None:
            for batch in table.combine_chunks().to_batches():
                self.profiler.process(batch)

    def prepared(self) -> dict[str, tuple[cmp._Prepared, bool]]:
        self.flush()
        if not self.dirty and self.cached:
            return self.cached
        infos: dict[str, _Info] = {}
        if self.profiler is not None and self.rows:
            for doc in self.profiler.peek().profile["columns"]:
                infos[doc["name"]] = _Info(doc.get("distinct"), doc.get("top"))
        self.cached = {
            name: col.prepared(infos.get(name, _Info())) for name, col in self.columns.items()
        }
        self.dirty = False
        return self.cached


# ---- alerts -----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LiveAlert:
    """One alert; ``to_dict`` is the documented line format (``docs/EMIT.md``)."""

    kind: str
    level: str  # error | warning | info
    message: str
    events: int
    table: str | None = None
    column: str | None = None
    score: float | None = None
    threshold: float | None = None
    time: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": ALERT_FORMAT,
            "kind": self.kind,
            "level": self.level,
            "table": self.table,
            "column": self.column,
            "score": None if self.score is None else round(self.score, 4),
            "threshold": self.threshold,
            "events": self.events,
            "time": self.time,
            "message": self.message,
        }


AlertSink = Callable[[LiveAlert], None]


def stderr_alert_sink(alert: LiveAlert) -> None:
    """The default sink: one line on standard error."""
    print(f"shape emit: ALERT [{alert.level}] {alert.kind}: {alert.message}", file=sys.stderr)


class JsonLinesAlertSink:
    """Alerts as JSON lines (``LiveAlert.to_dict``) appended to a file."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._f = open(self.path, "a", encoding="utf-8")  # noqa: SIM115 - closed by close()

    def __call__(self, alert: LiveAlert) -> None:
        self._f.write(json.dumps(alert.to_dict(), separators=(",", ":")) + "\n")
        self._f.flush()

    def close(self) -> None:
        if not self._f.closed:
            self._f.close()


# ---- the live comparison ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LiveSnapshot:
    events: int
    overall: float | None  # mean over the tables that have started; None before the first event
    tables: dict[str, float]  # the tables that have started
    pending: tuple[str, ...]  # tables with no event yet
    judged: dict[str, float]  # the tables with at least min_events: what the alerts look at
    judged_overall: float | None  # mean over ``judged`` once every table is in it, else None
    report: cmp.FidelityReport

    def to_dict(self) -> dict[str, Any]:
        return {
            "events": self.events,
            "overall": None if self.overall is None else round(self.overall, 6),
            "tables": {k: round(v, 6) for k, v in self.tables.items()},
            "pending": list(self.pending),
        }


class LiveFidelity:
    """Takes event batches, scores them against ``target``, raises alerts.

    ``observe(batch)`` takes a flat-event batch of one table. ``evaluate`` scores what has been
    seen and applies the alert rules; ``final`` does it once more at the end and returns the
    report. ``tables`` limits the comparison to the tables the stream emits."""

    def __init__(
        self,
        target: TargetShape,
        config: LiveConfig | None = None,
        *,
        tables: Sequence[str] | None = None,
        sinks: Sequence[AlertSink] = (),
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.target = target
        self.config = config or LiveConfig()
        self.expected = [t for t in target.tables if tables is None or t in set(tables)]
        if not self.expected:
            raise ShapeError("the target has none of the tables the stream emits")
        self.sinks = list(sinks)
        self._clock = clock
        self._tables: dict[str, _LiveTable] = {}
        self.events = 0
        self.alerts: list[LiveAlert] = []
        self.evaluations = 0
        self.failed: str | None = None
        self.observe_seconds = 0.0
        self._since_eval = 0
        self._last_eval = clock()
        self._active: dict[tuple[str, str | None, str | None], LiveAlert] = {}
        self._best: dict[str | None, float] = {}
        self.last: LiveSnapshot | None = None
        self.trajectory: list[dict[str, Any]] = []

    # ---- input --------------------------------------------------------------------------------

    def observe(self, batch: pa.RecordBatch) -> None:
        """Count a batch of events of one table. Never raises: a failure switches the live side
        off and is raised as a ``live-error`` alert."""
        if self.failed is not None or batch.num_rows == 0:
            return
        started = time.perf_counter()
        try:
            for name, part in self._split(batch):
                self._table(name, part).add(part)
            self.events += batch.num_rows
            self._since_eval += batch.num_rows
            if (
                self._since_eval >= self.config.interval_events
                and self._clock() - self._last_eval >= self.config.interval_seconds
            ):
                self.evaluate()
        except Exception as exc:
            self._fail(exc)
        finally:
            self.observe_seconds += time.perf_counter() - started

    def _split(self, batch: pa.RecordBatch) -> Iterable[tuple[str, pa.RecordBatch]]:
        names = batch.schema.names
        if FIELD_TABLE not in names:
            raise ShapeError(f"events lack {FIELD_TABLE}: this is not an emitted stream")
        column = batch.column(names.index(FIELD_TABLE))
        first = column[0].as_py()
        if pc.all(pc.equal(column, first)).as_py():
            return [(first, batch)]
        parts = []
        for table in pc.unique(column).to_pylist():
            parts.append((table, batch.filter(pc.equal(column, table))))
        return parts

    def _table(self, name: str, batch: pa.RecordBatch) -> _LiveTable:
        live = self._tables.get(name)
        if live is None:
            keep = [n for n in batch.schema.names if n not in _RESERVED]
            schema = pa.schema([batch.schema.field(n) for n in keep])
            live = self._tables[name] = _LiveTable(
                name, schema, self.config, self.config.seed + len(self._tables)
            )
        return live

    # ---- scoring ------------------------------------------------------------------------------

    def _table_report(self, name: str) -> cmp.TableFidelity:
        ref = self.target.columns[name]
        live = self._tables.get(name)
        if live is None or live.rows == 0:
            cols = {c: cmp._missing_column(c) for c in ref}
            return cmp.TableFidelity(
                name,
                self.target.rows[name],
                0,
                cols,
                0.0,
                tuple(ref),
                (),
                False,
                self.target.issues[name],
            )
        have = live.prepared()
        columns = {
            c: cmp.score_prepared(c, ref[c], have[c][0]) if c in have else cmp._missing_column(c)
            for c in ref
        }
        issues = self.target.issues[name]
        score = float(np.mean([c.score for c in columns.values()])) if columns else 0.0
        if issues:
            score = 0.0
        missing = tuple(c for c in ref if c not in have)
        extra = tuple(c for c in have if c not in ref)
        return cmp.TableFidelity(
            name, self.target.rows[name], live.rows, columns, score, missing, extra, True, issues
        )

    def snapshot(self) -> LiveSnapshot:
        """Score what has been seen (the events still waiting in a chunk are counted first)."""
        cfg = self.config
        tables = {name: self._table_report(name) for name in self.expected}
        judged = {
            n: t
            for n, t in tables.items()
            if self._tables.get(n) is not None
            and self._tables[n].rows >= max(cfg.min_events, cfg.min_progress * self.target.rows[n])
        }
        started = {
            n: t.score for n, t in tables.items() if self._tables.get(n) is not None and t.present
        }
        scores = {n: t.score for n, t in judged.items()}
        overall = float(np.mean(list(started.values()))) if started else None
        report = cmp.FidelityReport(
            tables,
            0.0 if overall is None else overall,
            tuple(n for n in self.expected if n not in self._tables),
            (),
            () if tables else ("the reference has no tables",),
            cmp.Thresholds(cfg.min_score, cfg.min_table_score, cfg.min_column_score),
        )
        pending = tuple(n for n in self.expected if n not in started)
        every = len(scores) == len(self.expected)
        judged_overall = float(np.mean(list(scores.values()))) if scores and every else None
        return LiveSnapshot(self.events, overall, started, pending, scores, judged_overall, report)

    def evaluate(self) -> LiveSnapshot:
        """Score now, apply the alert rules, remember the result."""
        snap = self.snapshot()
        self.evaluations += 1
        self._since_eval = 0
        self._last_eval = self._clock()
        self.last = snap
        entry = snap.to_dict()
        entry["alerts"] = len(self.alerts)
        self.trajectory.append(entry)
        self._apply_rules(snap)
        return snap

    def _apply_rules(self, snap: LiveSnapshot) -> None:
        cfg = self.config
        want: dict[tuple[str, str | None, str | None], LiveAlert] = {}
        now = datetime.now(UTC).isoformat()

        def alert(kind: str, level: str, msg: str, **kw: Any) -> None:
            key = (kind, kw.get("table"), kw.get("column"))
            want[key] = LiveAlert(kind, level, msg, snap.events, time=now, **kw)

        scope: list[tuple[str | None, float]] = [(n, s) for n, s in snap.judged.items()]
        if snap.judged_overall is not None:
            scope.append((None, snap.judged_overall))
        for table, score in scope:
            best = max(self._best.get(table, score), score)
            self._best[table] = best
            label = "overall" if table is None else f"table {table}"
            mark = cfg.min_score if table is None else cfg.min_table_score
            if score < mark:
                alert(
                    KIND_SCORE_LOW,
                    "error",
                    f"{label}: live score {score:.2f} < {mark:g} after {snap.events:,} events",
                    table=table,
                    score=score,
                    threshold=mark,
                )
            elif best - score >= cfg.drop:
                alert(
                    KIND_SCORE_DROP,
                    "warning",
                    f"{label}: live score {score:.2f} is {best - score:.2f} below its best "
                    f"{best:.2f} (drop limit {cfg.drop:g}) after {snap.events:,} events",
                    table=table,
                    score=score,
                    threshold=best - cfg.drop,
                )
        if cfg.min_column_score is not None:
            for table in snap.judged:
                for c in snap.report.tables[table].columns.values():
                    if c.present and c.score < cfg.min_column_score:
                        alert(
                            KIND_COLUMN_LOW,
                            "warning",
                            f"table {table}: column {c.column_name} live score {c.score:.2f} < "
                            f"{cfg.min_column_score:g} after {snap.events:,} events",
                            table=table,
                            column=c.column_name,
                            score=c.score,
                            threshold=cfg.min_column_score,
                        )
        for key, a in want.items():
            if key not in self._active:
                self._raise(a)
        for key, old in list(self._active.items()):
            if key not in want:
                self._raise(
                    LiveAlert(
                        KIND_RECOVERED,
                        "info",
                        f"{old.kind} cleared"
                        + ("" if old.table is None else f" for table {old.table}")
                        + ("" if old.column is None else f" column {old.column}"),
                        snap.events,
                        old.table,
                        old.column,
                        time=now,
                    )
                )
        self._active = want

    def _raise(self, alert: LiveAlert) -> None:
        self.alerts.append(alert)
        for sink in self.sinks:
            try:
                sink(alert)
            except Exception as exc:  # a broken alert sink must not stop the stream
                print(f"shape emit: alert sink failed: {exc}", file=sys.stderr)

    def _fail(self, exc: Exception) -> None:
        self.failed = f"{type(exc).__name__}: {exc}"
        self._raise(
            LiveAlert(
                KIND_LIVE_ERROR,
                "error",
                f"live fidelity switched off: {self.failed}",
                self.events,
                time=datetime.now(UTC).isoformat(),
            )
        )

    # ---- end ----------------------------------------------------------------------------------

    def final(self) -> LiveSnapshot:
        """Evaluate once more over everything seen; the report of the run."""
        if self.failed is not None and self.last is not None:
            return self.last
        try:
            return self.evaluate()
        except Exception as exc:
            self._fail(exc)
            raise

    def verdict(self) -> list[str]:
        """Why the run fails its live pass marks (empty: it does not): the final score of a table
        that has started or of the whole is below a mark, an error alert is still active at the
        end, or the live side failed. Tables that never started are not judged (a run that
        stopped early did not emit them)."""
        out: list[str] = []
        if self.failed is not None:
            out.append(f"live fidelity failed: {self.failed}")
        snap = self.last
        if snap is None:
            return out
        cfg = self.config
        if snap.overall is not None and snap.overall < cfg.min_score:
            out.append(f"overall score {snap.overall:.2f} < {cfg.min_score:g}")
        for name, score in snap.tables.items():
            if score < cfg.min_table_score:
                out.append(f"table {name}: score {score:.2f} < {cfg.min_table_score:g}")
            if cfg.min_column_score is not None:
                for c in snap.report.tables[name].columns.values():
                    if c.present and c.score < cfg.min_column_score:
                        out.append(
                            f"table {name}: column {c.column_name} score {c.score:.2f} "
                            f"< {cfg.min_column_score:g}"
                        )
        out.extend(
            a.message
            for a in self._active.values()
            if a.level == "error" and a.kind != KIND_SCORE_LOW
        )
        return out

    def approximate(self) -> dict[str, list[str]]:
        """Columns that used a bound (a sample, a sketch) instead of the exact value."""
        out: dict[str, list[str]] = {}
        for name, live in self._tables.items():
            cols = [c for c, (_, approx) in live.cached.items() if approx]
            if cols:
                out[name] = cols
        return out

    def profiles(self) -> dict[str, Any]:
        """The live stream profile of every table (the stream profiler's bounded documents)."""
        return {
            name: live.profiler.peek().profile
            for name, live in self._tables.items()
            if live.profiler is not None and live.rows
        }

    def summary(self) -> dict[str, Any]:
        snap = self.last
        return {
            "format": REPORT_FORMAT,
            "events": self.events,
            "overall": None if snap is None or snap.overall is None else snap.overall,
            "tables": {} if snap is None else snap.tables,
            "pending": [] if snap is None else list(snap.pending),
            "evaluations": self.evaluations,
            "alerts": [a.to_dict() for a in self.alerts],
            "alert_count": sum(1 for a in self.alerts if a.kind != KIND_RECOVERED),
            "error_alerts": sum(1 for a in self.alerts if a.level == "error"),
            "approximate": self.approximate(),
            "failed": self.failed,
            "observe_seconds": self.observe_seconds,
            "thresholds": {
                "min_progress": self.config.min_progress,
                "min_score": self.config.min_score,
                "min_table_score": self.config.min_table_score,
                "min_column_score": self.config.min_column_score,
                "drop": self.config.drop,
                "min_events": self.config.min_events,
            },
            "trajectory": self.trajectory,
        }


# ---- the tee ----------------------------------------------------------------------------------


class TeeSink:
    """A sink that delivers to ``inner`` and shows each delivered batch to a ``LiveFidelity``.

    The live side sees a batch only after ``inner.send`` returned, so a batch the runtime
    retries is counted once, and nothing the live side does can fail a delivery."""

    def __init__(self, inner: EventSink, live: LiveFidelity) -> None:
        self.inner = inner
        self.live = live

    def send(self, batch: pa.RecordBatch) -> None:
        self.inner.send(batch)
        self.live.observe(batch)

    def flush(self) -> None:
        self.inner.flush()

    def close(self) -> None:
        try:
            if self.live.failed is None and self.live.events:
                self.live.final()
        finally:
            self.inner.close()


__all__ = [
    "ALERT_FORMAT",
    "REPORT_FORMAT",
    "AlertSink",
    "JsonLinesAlertSink",
    "LiveAlert",
    "LiveConfig",
    "LiveFidelity",
    "LiveSnapshot",
    "TargetShape",
    "TeeSink",
    "stderr_alert_sink",
]
