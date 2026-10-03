"""Row-level anomaly injection for streams and batches: the entry point behind
``--anomaly-fraction``.

``inject_anomalies`` takes one Arrow batch and corrupts about ``fraction`` of its rows, each with
one anomaly drawn from the schema-preserving kinds of the value category. The result has the
batch's schema and row count, so it can be emitted as it is. The result lists which rows were
changed and how, so a caller can label or count them.

Determinism: the output depends only on the batch, ``fraction``, ``seed`` and ``kinds``. A caller
that processes a stream derives one seed per batch (for example from the run seed and the batch
sequence number), which makes a replay produce the same anomalies.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]

from shape.chaos.categories import (
    BOM,
    LATIN_CHARS,
    _datetime_array,
    _datetime_values,
    _is_number,
    _is_text,
    extreme_range,
)
from shape.plugins.api.v1 import ChaosReport

SHAPE_API = "1.0"

#: The anomaly kinds, in the order they are tried. Each keeps the column's type.
KINDS: tuple[str, ...] = ("null", "out_of_range", "negative", "future_date", "encoding")

_FUTURE_START = np.datetime64("2030-01-01", "D")


@dataclass(frozen=True, slots=True)
class AnomalyResult:
    """The mutated ``batch``, the changed ``rows`` (ascending positions) and the ``kinds`` and
    ``columns`` that hit them (aligned with ``rows``), plus the usual ``report``."""

    batch: pa.RecordBatch
    rows: tuple[int, ...]
    kinds: tuple[str, ...]
    columns: tuple[str, ...]
    report: ChaosReport


def _eligible(batch: pa.RecordBatch, kind: str, protect: frozenset[str]) -> list[int]:
    out: list[int] = []
    for i, f in enumerate(batch.schema):
        if f.name in protect:
            continue
        t = f.type
        if kind == "null":
            ok = bool(f.nullable)
        elif kind == "out_of_range":
            ok = _is_number(t)
        elif kind == "negative":
            ok = bool(pa.types.is_floating(t) or pa.types.is_signed_integer(t))
        elif kind == "future_date":
            ok = bool(pa.types.is_timestamp(t) or pa.types.is_date32(t))
        else:
            ok = _is_text(t)
        if ok:
            out.append(i)
    return out


def _null_rows(arr: pa.Array, pos: np.ndarray) -> tuple[pa.Array, np.ndarray]:
    keep = np.ones(len(arr), dtype=bool)
    keep[pos] = False
    was_valid = np.array(arr.is_valid().to_numpy(zero_copy_only=False), dtype=bool)
    changed = pos[was_valid[pos]]
    return arr.take(pa.array(np.arange(len(arr)), mask=~keep)), changed


def _numeric_values(arr: pa.Array) -> tuple[np.ndarray, np.ndarray]:
    valid = np.array(arr.is_valid().to_numpy(zero_copy_only=False), dtype=bool)
    filled = pc.fill_null(arr, pa.scalar(0, arr.type))
    return np.array(filled.to_numpy(zero_copy_only=False)), valid


def _out_of_range(
    arr: pa.Array, pos: np.ndarray, rng: np.random.Generator
) -> tuple[pa.Array, np.ndarray]:
    values, valid = _numeric_values(arr)
    peak = pc.max(pc.abs(arr)).as_py()
    baseline = float(peak) if peak and np.isfinite(peak) else 1000.0
    extreme = rng.uniform(*extreme_range(baseline), size=len(pos))
    if pa.types.is_integer(arr.type):
        info = np.iinfo(values.dtype)
        top = np.nextafter(float(info.max), 0.0)
        extreme = np.clip(extreme, float(info.min), top)
    elif arr.type.bit_width < 64:
        extreme = np.clip(extreme, -np.finfo(values.dtype).max, np.finfo(values.dtype).max)
    values[pos] = extreme.astype(values.dtype)
    valid[pos] = True
    return pa.array(values, type=arr.type, mask=~valid), pos


def _negative(
    arr: pa.Array, pos: np.ndarray, rng: np.random.Generator
) -> tuple[pa.Array, np.ndarray]:
    values, valid = _numeric_values(arr)
    changed = pos[valid[pos] & (values[pos] != 0)]
    values[changed] = -values[changed]
    return pa.array(values, type=arr.type, mask=~valid), changed


def _future_date(
    arr: pa.Array, pos: np.ndarray, rng: np.random.Generator
) -> tuple[pa.Array, np.ndarray]:
    offsets = rng.integers(365, 3650, size=len(pos)).astype("timedelta64[D]")
    future = _FUTURE_START + offsets
    if pa.types.is_date32(arr.type):
        days = np.array(pc.fill_null(arr, pa.scalar(0, arr.type)).cast(pa.int32()))
        valid = np.array(arr.is_valid().to_numpy(zero_copy_only=False), dtype=bool)
        days[pos] = future.astype("datetime64[D]").astype(np.int32)
        valid[pos] = True
        return pa.array(days, type=pa.int32(), mask=~valid).cast(arr.type), pos
    values, valid = _datetime_values(arr)
    values[pos] = future.astype(values.dtype)
    valid[pos] = True
    return _datetime_array(values, valid, arr.type), pos


def _encoding(
    arr: pa.Array, pos: np.ndarray, rng: np.random.Generator
) -> tuple[pa.Array, np.ndarray]:
    cells = arr.to_pylist()
    flip = rng.integers(0, 2, size=len(pos))
    marks = rng.integers(0, len(LATIN_CHARS), size=len(pos))
    changed: list[int] = []
    for p, f, m in zip(pos.tolist(), flip.tolist(), marks.tolist(), strict=True):
        v = cells[p]
        if v is None:
            continue
        cells[p] = BOM + v if f == 0 else v + LATIN_CHARS[m]
        changed.append(p)
    return pa.array(cells, type=arr.type), np.asarray(changed, dtype=np.int64)


_APPLY: dict[
    str, Callable[[pa.Array, np.ndarray, np.random.Generator], tuple[pa.Array, np.ndarray]]
] = {
    "out_of_range": _out_of_range,
    "negative": _negative,
    "future_date": _future_date,
    "encoding": _encoding,
}


def inject_anomalies(
    batch: pa.RecordBatch,
    *,
    fraction: float,
    seed: int,
    kinds: Sequence[str] | None = None,
    protect: Sequence[str] = (),
) -> AnomalyResult:
    """Corrupt about ``fraction`` of the rows of ``batch``, one anomaly each.

    ``fraction`` is in [0, 1]. The number of rows targeted is ``n * fraction`` rounded
    stochastically (so the expected rate is exact on any batch size). Each targeted row gets one
    kind chosen uniformly among the kinds the batch can take (``kinds`` narrows the choice to a
    subset of :data:`KINDS`), applied to one column chosen uniformly among the eligible ones.

    * ``null``: the cell becomes null (nullable fields only).
    * ``out_of_range``: a number 100 to 1000 times the column's largest magnitude (integers are
      clipped to their range).
    * ``negative``: a number's sign flips (signed integer and float columns; zero is unchanged).
    * ``future_date``: a timestamp or ``date32`` between 2031 and 2039 (``date64`` is left alone).
    * ``encoding``: a text cell gets a leading byte-order mark or a trailing Latin-1 character.

    ``protect`` names columns that are never touched (a stream's key and event-time columns).
    A row whose chosen cell cannot change (a null text or number being negated) is left as it
    was and is not counted, so ``len(result.rows)`` is the number of rows actually changed. The
    input batch is not modified.
    """
    if not 0.0 <= fraction <= 1.0:
        raise ValueError(f"fraction must be in [0, 1], got {fraction}")
    wanted = KINDS if kinds is None else tuple(kinds)
    unknown = [k for k in wanted if k not in KINDS]
    if unknown:
        raise ValueError(f"unknown anomaly kind(s) {unknown}; choose from {list(KINDS)}")
    n = batch.num_rows
    rng = np.random.default_rng(seed)
    held = frozenset(protect)
    plan = [(k, c) for k in KINDS if k in wanted if (c := _eligible(batch, k, held))]
    # Draws happen in a fixed order whatever the batch holds, so the result is reproducible.
    exact = n * fraction
    count = int(exact) + (1 if float(rng.random()) < exact - int(exact) else 0)
    count = min(count, n)
    targets = (
        np.sort(np.asarray(rng.choice(n, size=count, replace=False)))
        if count
        else np.empty(0, dtype=np.int64)
    )
    pick_kind = rng.random(count)
    pick_col = rng.random(count)
    if not plan or count == 0:
        details = {"fraction": fraction, "requested": int(count), "kinds": {}}
        return AnomalyResult(batch, (), (), (), ChaosReport("anomalies", 0, details))

    kind_of = np.minimum((pick_kind * len(plan)).astype(np.int64), len(plan) - 1)
    columns = list(batch.columns)
    rows: list[tuple[int, str, str]] = []
    for p, (kind, cols) in enumerate(plan):
        sel = targets[kind_of == p]
        if sel.size == 0:
            continue
        which = np.minimum((pick_col[kind_of == p] * len(cols)).astype(np.int64), len(cols) - 1)
        for j, ci in enumerate(cols):
            pos = sel[which == j]
            if pos.size == 0:
                continue
            arr = columns[ci]
            if kind == "null":
                new, changed = _null_rows(arr, pos)
            else:
                new, changed = _APPLY[kind](arr, pos, rng)
            columns[ci] = new
            name = batch.schema.field(ci).name
            rows.extend((int(r), kind, name) for r in changed.tolist())
    rows.sort()
    out = pa.RecordBatch.from_arrays(columns, schema=batch.schema)
    by_kind: dict[str, int] = {}
    for _, kind, _ in rows:
        by_kind[kind] = by_kind.get(kind, 0) + 1
    report = ChaosReport(
        "anomalies",
        len(rows),
        {"fraction": fraction, "requested": int(count), "kinds": by_kind},
    )
    return AnomalyResult(
        out,
        tuple(r for r, _, _ in rows),
        tuple(k for _, k, _ in rows),
        tuple(c for _, _, c in rows),
        report,
    )
