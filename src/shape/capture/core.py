"""Capture: the legacy per-column summary of row dicts and column arrays, on the profile kernel.

``capture_rows`` and ``capture_columns`` are edge adapters (P1-12): they turn Python rows or
columns into Arrow, hand them to the fused profile kernel (``ProfileState``) one record batch at
a time, and shape what comes back as the capture document the v1 consumers read
(``{"rows": n, "columns": {name: {"kind": "numeric" | "text", ...}}}``). The profile engine
(``shape.profile.engine``) is the one implementation of the statistics; nothing here computes
them. Nulls are real nulls (P10); text goes through the kernel, not cell by cell (P13); both
entry points emit the same schema (P12).

``capture_rows`` makes a single bounded pass over its input. The type of a column is decided at
the end, so a leading null (P3), numpy or Decimal numbers (P4) and a late text value (P2) cannot
change or lose what was seen: every column is fed to the kernel twice, as text, and as numbers
while it is still all-numeric.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]

from shape.kernel.dispatch import get_kernel
from shape.profile.error import ErrorModel, hll_error, kll_error
from shape.profile.infer import as_number, is_number


@dataclass(frozen=True, slots=True)
class CapturedShape:
    rows: int
    columns: dict[str, dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {"rows": self.rows, "columns": self.columns}


_TOP = 10
_INT64_MIN, _INT64_MAX = -(2**63), 2**63 - 1
_EXACT_CARD = ErrorModel("exact-hash-table", True)
_EXACT_QUANTILE = ErrorModel("exact-sort", True)


def _topk(top: list[Any]) -> list[list[Any]]:
    return [[value, count, error] for value, count, error, _first in top[:_TOP]]


def _distinct(stats: dict[str, Any]) -> float | int:
    d = stats["distinct"]
    return int(d) if stats["distinct_exact"] else float(d)


def _cardinality(stats: dict[str, Any]) -> dict[str, Any]:
    model = _EXACT_CARD if stats["distinct_exact"] else hll_error(14)
    return model.to_dict()


def _numeric(stats: dict[str, Any]) -> dict[str, Any]:
    finite = stats["finite_count"]
    q = stats["quantiles"]
    exact = stats["distinct_exact"]
    return {
        "kind": "numeric",
        "count": stats["count"],
        "null_count": stats["null_count"],
        "nan_count": stats["nan_count"],
        "pos_inf_count": stats["pos_inf_count"],
        "neg_inf_count": stats["neg_inf_count"],
        "finite_count": finite,
        "min": stats["min"],
        "max": stats["max"],
        "mean": stats["mean"] if finite else None,
        "variance_population": stats["m2"] / finite if finite else None,
        "q25": q.get(0.25),
        "q50": q.get(0.5),
        "q75": q.get(0.75),
        "distinct_estimate": _distinct(stats),
        "topk": _topk(stats["top"]),
        "error_models": {
            "cardinality": _cardinality(stats),
            "quantiles": (_EXACT_QUANTILE if exact else kll_error(200)).to_dict(),
        },
    }


def _hist_quantile(hist: dict[int, int], total: int, q: float) -> float:
    """numpy's linear percentile over a histogram of integer values."""
    pos = q * (total - 1)
    lo = math.floor(pos)
    hi = min(lo + 1, total - 1)
    t = pos - lo

    def at(rank: int) -> float:
        acc = 0
        for value in sorted(hist):
            acc += hist[value]
            if rank < acc:
                return float(value)
        return 0.0

    a, b = at(lo), at(hi)
    return b - (b - a) * (1 - t) if t >= 0.5 else a + (b - a) * t


def _length_summary(length: dict[str, Any], exact: bool) -> dict[str, Any]:
    n = length["count"]
    hist: dict[int, int] = length.get("hist", {})
    out: dict[str, Any] = {
        "count": n,
        "null_count": 0,
        "nan_count": 0,
        "pos_inf_count": 0,
        "neg_inf_count": 0,
        "finite_count": n,
        "min": None,
        "max": None,
        "mean": None,
        "variance_population": None,
        "q25": None,
        "q50": None,
        "q75": None,
        "distinct_estimate": float(len(hist)),
        "topk": [],
    }
    if n:
        mean = length["mean"]
        m2 = sum(c * (v - mean) ** 2 for v, c in hist.items())
        top = sorted(hist.items(), key=lambda kv: (-kv[1], kv[0]))[:_TOP]
        out.update(
            min=length["min"],
            max=length["max"],
            mean=mean,
            variance_population=m2 / n,
            q25=_hist_quantile(hist, n, 0.25),
            q50=_hist_quantile(hist, n, 0.5),
            q75=_hist_quantile(hist, n, 0.75),
            topk=[[v, c, 0] for v, c in top],
        )
    return out


def _text(stats: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "text",
        "count": stats["count"],
        "null_count": stats["null_count"],
        "length": _length_summary(stats["length"], stats["distinct_exact"]),
        "distinct_estimate": _distinct(stats),
        "topk": _topk(stats["top"]),
        "error_models": {"cardinality": _cardinality(stats)},
    }


def _as_arrow(values: Any) -> Any:
    """An Arrow array for one input column, or None when it has no single Arrow type (for
    example a list mixing numbers and text): the row path then handles it."""
    if isinstance(values, (pa.Array, pa.ChunkedArray)):
        return values
    try:
        return pa.array(values)
    except (pa.ArrowInvalid, pa.ArrowTypeError, TypeError, ValueError):
        return None


def capture_columns(columns: dict[str, Any], mode: str = "exact") -> dict[str, Any]:
    """Profile equal-length column arrays. ``mode`` is ``"exact"`` (default) or ``"bounded"``
    (sketches for distinct counts, top values and quantiles)."""
    if not columns:
        return {"rows": 0, "columns": {}}
    if len({len(v) for v in columns.values()}) != 1:
        raise ValueError("columns must have equal length")
    n = len(next(iter(columns.values())))
    arrays = {name: _as_arrow(v) for name, v in columns.items()}
    fast = {
        name: a
        for name, a in arrays.items()
        if a is not None
        and (
            pa.types.is_integer(a.type)
            or pa.types.is_floating(a.type)
            or pa.types.is_decimal128(a.type)
            or pa.types.is_string(a.type)
            or pa.types.is_large_string(a.type)
        )
    }
    out: dict[str, Any] = {}
    if fast:
        table = pa.table(fast)
        state = get_kernel().ProfileState(table.schema, mode)
        for batch in table.to_batches():
            state.update(batch)
        for stats in state.finalize()["columns"]:
            out[stats["name"]] = (
                _numeric(stats) if stats["kind"] in ("int", "float") else _text(stats)
            )
    for name, values in columns.items():
        if name in out:
            continue
        # correctness fallback: the row path is the schema reference for the other kinds
        items = arrays[name].to_pylist() if arrays[name] is not None else list(values)
        if not items:  # no rows: the column is still described, as capture_arrow does (#687)
            out[name] = _ColumnState(name, "bounded").summary()
            continue
        out[name] = capture_rows({name: x} for x in items).to_dict()["columns"][name]
    return {"rows": n, "columns": {name: out[name] for name in columns}}


class _ColumnState:
    """One column of ``capture_rows``: its kernel states and what has been seen."""

    def __init__(self, name: str, mode: str) -> None:
        kernel = get_kernel()
        self.name = name
        self.text_schema = pa.schema([(name, pa.string())])
        self.num_schema = pa.schema([(name, pa.float64())])
        self.int_schema = pa.schema([(name, pa.int64())])
        self.text = kernel.ProfileState(self.text_schema, mode)
        self.num = kernel.ProfileState(self.num_schema, mode)
        # integers are also fed exactly while every number is one that fits int64 (#685): a
        # float64 loses integers past 2**53 (ids), so the extremes and distinct values would too
        self.ints = kernel.ProfileState(self.int_schema, mode)
        self.numeric_count = 0
        self.other_count = 0
        self.numeric_ok = True  # every non-null value so far is a number
        self.int_ok = True  # every number so far is an integer within int64

    @property
    def is_numeric(self) -> bool:
        """At least one number and nothing else (nulls do not count)."""
        return self.numeric_count > 0 and self.other_count == 0

    def feed_nulls(self, n: int) -> None:
        self.text.update(pa.record_batch([pa.nulls(n, pa.string())], schema=self.text_schema))
        self.num.update(pa.record_batch([pa.nulls(n, pa.float64())], schema=self.num_schema))
        self.ints.update(pa.record_batch([pa.nulls(n, pa.int64())], schema=self.int_schema))

    def _text_of(self, v: Any) -> str | None:
        if v is None:
            return None
        if isinstance(v, (bytes, bytearray, memoryview)):  # binary: its UTF-8 text (#688)
            try:
                return bytes(v).decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError(
                    f"column {self.name!r} holds bytes that are not UTF-8 text ({exc.reason} at "
                    f"byte {exc.start}): convert the file to UTF-8 first"
                ) from None
        return str(v)

    def feed(self, values: list[Any]) -> None:
        text = [self._text_of(v) for v in values]
        self.text.update(pa.record_batch([pa.array(text, pa.string())], schema=self.text_schema))
        numbers: list[float | None] = []
        ints: list[int | None] = []
        for v in values:
            if v is None:
                numbers.append(None)
                ints.append(None)
            elif is_number(v):
                self.numeric_count += 1
                n = as_number(v)
                numbers.append(float(n))
                if isinstance(n, int) and _INT64_MIN <= n <= _INT64_MAX:
                    ints.append(n)
                else:
                    self.int_ok = False
                    ints.append(None)
            else:
                self.other_count += 1
                self.numeric_ok = False
                numbers.append(None)
                ints.append(None)
        if self.numeric_ok:  # once a text value has been seen the numeric side is never used
            self.num.update(
                pa.record_batch([pa.array(numbers, pa.float64())], schema=self.num_schema)
            )
            if self.int_ok:
                self.ints.update(
                    pa.record_batch([pa.array(ints, pa.int64())], schema=self.int_schema)
                )

    def summary(self) -> dict[str, Any]:
        if self.is_numeric:
            state = self.ints if self.int_ok else self.num
            return _numeric(state.finalize()["columns"][0])
        return _text(self.text.finalize()["columns"][0])


def capture_rows(rows: Iterable[Mapping[str, Any]], batch_size: int = 10000) -> CapturedShape:
    """One bounded pass over row dicts, ``batch_size`` rows per kernel call."""
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    columns: dict[str, _ColumnState] = {}
    seen = 0
    batch: list[Mapping[str, Any]] = []

    def flush() -> None:
        nonlocal seen
        for row in batch:
            for key in row:
                if key not in columns:
                    col = columns[key] = _ColumnState(key, "bounded")
                    for start in range(0, seen, batch_size):  # rows before it first appeared
                        col.feed_nulls(min(batch_size, seen - start))
        for name, col in columns.items():
            col.feed([row.get(name) for row in batch])
        seen += len(batch)
        batch.clear()

    for row in rows:
        batch.append(row)
        if len(batch) >= batch_size:
            flush()
    if batch:
        flush()
    return CapturedShape(seen, {name: col.summary() for name, col in columns.items()})


def capture_arrow(table: pa.Table, batch_size: int = 10000) -> CapturedShape:
    """The capture of an Arrow table (what ``shape capture`` reads from any source). It goes
    through :func:`capture_rows`, so a table gives the same document whether it was read from a
    CSV, a Parquet file or a Delta table; a table with no rows still has its columns (as text)."""
    if table.num_rows == 0:
        return CapturedShape(
            0, {name: _ColumnState(name, "bounded").summary() for name in table.schema.names}
        )

    def rows() -> Iterable[Mapping[str, Any]]:
        for batch in table.to_batches(max_chunksize=batch_size):
            yield from batch.to_pylist()

    return capture_rows(rows(), batch_size)
