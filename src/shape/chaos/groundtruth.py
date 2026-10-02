"""Named corruptions with a rate and a seed, and the ground-truth log of what they changed.

``corrupt_tables`` applies a list of :class:`Corruption` to Arrow tables and returns the corrupted
tables with one record per change: the table, the row (its position in the output table), the key of
the row, the column, what the cell was, what it is now, the kind and the seed. A quality check can
be scored against that log because it is the whole truth: a cell that is not in it is untouched.

The corruptions model what a source system does to a delivery:

=================  =================================================================================
``duplicates``     rows delivered twice (at-least-once delivery), appended at the end of the table
``orphan_keys``    foreign keys that match no parent row
``date_shift``     late-arriving or wrongly dated rows (a date or timestamp moved by up to N days)
``negative_amounts``  sign flips of positive amounts
``case_whitespace``   inconsistent categories (upper, lower, leading or trailing blanks)
``pii_fill``       a free-text column filled with SSN-format (or email, phone) values
``type_change``    a column delivered as text
``null_creep``     a null rate that ramps up from batch to batch
=================  =================================================================================

``rate`` is the share of the table's rows changed (exact: ``round(rows x rate)``, capped at the rows
that can change). A corruption is active in batches ``start_batch`` to ``end_batch``; ``null_creep``
adds ``step`` to its rate for every batch after the start. Rows are never reordered or removed, so a
position in the log is a position in the output.

Determinism: every corruption draws from its own generator, seeded by ``(seed, batch, kind, table,
column)`` (and, for the same kind twice, its occurrence), so a seed gives the same corruption and
the same log, and adding a corruption of another kind does not change the others.

The six categories of :mod:`shape.chaos.categories` (schema, value, file, referential, temporal and
volume) are the scheduler's randomised mutators, bound to the baseline's draw order; this module is
the targeted form, where a named corruption has an exact rate and a log. It reuses their column
helpers and the orphan-key writer.
"""

from __future__ import annotations

import datetime as dt
import decimal
import json
import zlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]

from shape.chaos.categories import ORPHAN_BASE, _col, _put, _write_orphans

CORRUPTIONS = (
    "duplicates",
    "orphan_keys",
    "date_shift",
    "negative_amounts",
    "case_whitespace",
    "pii_fill",
    "type_change",
    "null_creep",
)
PII_KINDS = ("ssn", "email", "phone")
_TEXT_HINTS = ("note", "comment", "text", "desc", "memo", "remark", "message")
_OPTIONS = {
    "date_shift": {"days", "direction"},
    "pii_fill": {"pii"},
    "null_creep": {"step"},
}
_UNIT_PER_DAY = {"s": 86_400, "ms": 86_400_000, "us": 86_400_000_000, "ns": 86_400_000_000_000}
LOG_VERSION = 1


@dataclass(frozen=True)
class Corruption:
    """One named corruption.

    Args:
        kind: A member of :data:`CORRUPTIONS`.
        rate: The share of the table's rows to change, 0 to 1.
        table: The table; ``None`` means every table the corruption applies to.
        column: The column; ``None`` means the columns the kind picks by itself (a date shift: every
            date column; negative amounts: every amount column; orphan keys: every foreign key;
            case or whitespace: every low-cardinality text column). ``pii_fill``, ``type_change``
            and ``null_creep`` need a column.
        start_batch: First batch the corruption is active in.
        end_batch: Last batch (inclusive); ``None`` means no end.
        options: ``days`` and ``direction`` (``both``, ``late`` or ``early``) of a date shift,
            ``pii`` (``ssn``, ``email`` or ``phone``) of a PII fill, ``step`` (rate added per batch)
            of null creep.
    """

    kind: str
    rate: float = 0.02
    table: str | None = None
    column: str | None = None
    start_batch: int = 0
    end_batch: int | None = None
    options: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kind not in CORRUPTIONS:
            raise ValueError(
                f"unknown corruption {self.kind!r}; choose one of {', '.join(CORRUPTIONS)}"
            )
        if not 0.0 <= self.rate <= 1.0:
            raise ValueError(f"the rate of {self.kind} is 0 to 1, got {self.rate}")
        if self.start_batch < 0 or (
            self.end_batch is not None and self.end_batch < self.start_batch
        ):
            raise ValueError(f"{self.kind}: the batches are start <= end, both 0 or more")
        extra = set(self.options) - _OPTIONS.get(self.kind, set())
        if extra:
            raise ValueError(f"{self.kind} has no option {', '.join(sorted(extra))}")
        if self.kind == "date_shift":
            if int(self.options.get("days", 7)) < 1:
                raise ValueError("date_shift days must be at least 1")
            if self.options.get("direction", "both") not in ("both", "late", "early"):
                raise ValueError("date_shift direction is both, late or early")
        if self.kind == "pii_fill" and self.options.get("pii", "ssn") not in PII_KINDS:
            raise ValueError(f"pii_fill pii is one of {', '.join(PII_KINDS)}")
        if self.kind == "null_creep" and float(self.options.get("step", 0.0)) < 0:
            raise ValueError("null_creep step cannot be negative")
        if self.kind in ("pii_fill", "type_change", "null_creep") and self.column is None:
            raise ValueError(f"{self.kind} needs a column: {self.kind}=RATE@TABLE.COLUMN")

    def active(self, batch: int) -> bool:
        """True when the corruption applies in ``batch``."""
        return batch >= self.start_batch and (self.end_batch is None or batch <= self.end_batch)

    def rate_in(self, batch: int) -> float:
        """The rate in ``batch``: null creep ramps by ``step`` a batch, capped at 1."""
        if self.kind == "null_creep":
            ramp = float(self.options.get("step", 0.0)) * (batch - self.start_batch)
            return min(1.0, self.rate + ramp)
        return self.rate

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "rate": self.rate,
            "table": self.table,
            "column": self.column,
            "start_batch": self.start_batch,
            "end_batch": self.end_batch,
            "options": dict(self.options),
        }

    @classmethod
    def parse(cls, text: str) -> Corruption:
        """``KIND[=RATE][@TABLE[.COLUMN]][:OPT=V[,OPT=V...]]``, for example
        ``pii_fill=0.05@customer.notes`` or ``date_shift=0.03@order.order_date:days=14,from=2``.
        ``from`` and ``to`` are the first and last batch."""
        body, _, opts = text.partition(":")
        head, _, target = body.partition("@")
        kind, eq, rate = head.partition("=")
        kwargs: dict[str, Any] = {"kind": kind.strip()}
        if eq:
            try:
                kwargs["rate"] = float(rate)
            except ValueError:
                raise ValueError(f"the rate in {text!r} must be a number") from None
        if target:
            table, dot, column = target.partition(".")
            kwargs["table"] = table or None
            if dot:
                kwargs["column"] = column or None
        options: dict[str, Any] = {}
        for item in filter(None, opts.split(",")):
            key, sep, value = item.partition("=")
            if not sep:
                raise ValueError(f"an option in {text!r} is NAME=VALUE, got {item!r}")
            key = key.strip()
            if key in ("from", "to"):
                kwargs["start_batch" if key == "from" else "end_batch"] = int(value)
            elif key in ("days",):
                options[key] = int(value)
            elif key == "step":
                options[key] = float(value)
            else:
                options[key] = value.strip()
        kwargs["options"] = options
        return cls(**kwargs)


@dataclass
class ChaosOutcome:
    """The corrupted tables, the ground-truth records and a summary of what was applied."""

    tables: dict[str, pa.Table]
    records: list[dict[str, Any]]
    applied: list[dict[str, Any]]
    seed: int
    batch: int
    corruptions: list[Corruption]
    rows_in: dict[str, int]

    def header(self) -> dict[str, Any]:
        """The first record of the log: the run (seed, batch, corruptions, table sizes)."""
        return {
            "record": "run",
            "log_version": LOG_VERSION,
            "tool": "shape.chaos",
            "seed": self.seed,
            "batch": self.batch,
            "corruptions": [c.to_dict() for c in self.corruptions],
            "tables": {
                name: {"rows_in": self.rows_in[name], "rows_out": t.num_rows}
                for name, t in self.tables.items()
            },
            "changes": len(self.records),
        }


def parse_corruptions(items: Sequence[str]) -> list[Corruption]:
    """Parse the ``--corrupt`` arguments; an empty list is an error."""
    if not items:
        raise ValueError(f"name at least one corruption: {', '.join(CORRUPTIONS)}")
    return [Corruption.parse(item) for item in items]


# ---- cell values for the log ------------------------------------------------------------------


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if np.isfinite(value) else str(value)
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return str(value)
    if isinstance(value, bytes):
        return value.hex()
    return str(value)


def _cells(col: pa.Array, rows: np.ndarray) -> list[Any]:
    return [_jsonable(v) for v in col.take(pa.array(rows)).to_pylist()]


# ---- column choice ----------------------------------------------------------------------------


def _is_text(t: pa.DataType) -> bool:
    return bool(pa.types.is_string(t) or pa.types.is_large_string(t))


def _is_amount(t: pa.DataType) -> bool:
    return bool(pa.types.is_floating(t) or pa.types.is_decimal(t) or pa.types.is_integer(t))


def _is_date(t: pa.DataType) -> bool:
    return bool(pa.types.is_timestamp(t) or pa.types.is_date(t))


class _Run:
    """The state of one ``corrupt_tables`` call."""

    def __init__(
        self,
        tables: Mapping[str, pa.Table],
        seed: int,
        batch: int,
        keys: Mapping[str, str],
        references: Mapping[str, str],
    ) -> None:
        self.tables = dict(tables)
        self.seed = seed
        self.batch = batch
        self.keys = dict(keys)
        self.references = dict(references)
        self.records: list[dict[str, Any]] = []
        self.applied: list[dict[str, Any]] = []

    def key_column(self, table: str) -> str | None:
        if table in self.keys:
            return self.keys[table]
        names = self.tables[table].column_names
        return names[0] if names else None

    def foreign_keys(self, table: str) -> list[str]:
        declared = [r.split(".", 1)[1] for r in self.references if r.startswith(f"{table}.")]
        if declared:
            return declared
        key = self.key_column(table)
        return [c for c in self.tables[table].column_names if c.endswith("_id") and c != key]

    def record(
        self,
        table: str,
        corruption: Corruption,
        rows: np.ndarray,
        column: str | None,
        before: list[Any] | None,
        after: list[Any] | None,
        extra: list[dict[str, Any]] | None = None,
    ) -> None:
        key_name = self.key_column(table)
        keys: list[Any] = [None] * len(rows)
        if key_name is not None and key_name in self.tables[table].column_names and len(rows):
            keys = _cells(
                _col(self.tables[table], self.tables[table].column_names.index(key_name)), rows
            )
        for n, row in enumerate(rows.tolist()):
            rec: dict[str, Any] = {
                "record": "change",
                "seed": self.seed,
                "batch": self.batch,
                "table": table,
                "kind": corruption.kind,
                "scope": "row",
                "row": int(row),
                "key": keys[n],
                "column": column,
                "before": before[n] if before is not None else None,
                "after": after[n] if after is not None else None,
            }
            if extra is not None:
                rec.update(extra[n])
            self.records.append(rec)
        self.applied.append(
            {"kind": corruption.kind, "table": table, "column": column, "rows": int(len(rows))}
        )


def _rng(seed: int, batch: int, pos: int, table: str, column: str | None) -> np.random.Generator:
    return np.random.default_rng(
        [seed, batch, pos, zlib.crc32(table.encode()), zlib.crc32((column or "").encode())]
    )


def _pick(rng: np.random.Generator, eligible: np.ndarray, n_rows: int, rate: float) -> np.ndarray:
    """``round(n_rows x rate)`` rows of ``eligible`` (fewer when fewer can change), ascending."""
    k = min(int(round(n_rows * rate)), len(eligible))
    if k <= 0:
        return np.empty(0, dtype=np.int64)
    return np.sort(np.asarray(rng.choice(eligible, size=k, replace=False), dtype=np.int64))


def _valid(col: pa.Array) -> np.ndarray:
    return np.array(col.is_valid().to_numpy(zero_copy_only=False), dtype=bool)


def _targets(run: _Run, c: Corruption) -> list[str]:
    names = list(run.tables)
    if c.table is not None:
        if c.table not in run.tables:
            raise ValueError(
                f"{c.kind}: table {c.table!r} is not among the tables ({', '.join(names)})"
            )
        return [c.table]
    if c.column is not None:
        have = [n for n in names if c.column in run.tables[n].column_names]
        if not have:
            raise ValueError(f"{c.kind}: no table has a column {c.column!r}")
        return have
    return names


def _columns(run: _Run, c: Corruption, table: str) -> list[str]:
    """The columns of ``table`` the corruption works on; an explicit one must be there and fit."""
    t = run.tables[table]
    if c.column is not None:
        if c.column not in t.column_names:
            raise ValueError(f"{c.kind}: table {table!r} has no column {c.column!r}")
        return [c.column]
    key = run.key_column(table)
    fks = set(run.foreign_keys(table))
    out: list[str] = []
    for f in t.schema:
        if c.kind == "date_shift" and _is_date(f.type):
            out.append(f.name)
        elif c.kind == "negative_amounts" and _is_amount(f.type):
            if f.name != key and f.name not in fks and not f.name.endswith(("_id", "_key")):
                out.append(f.name)
        elif c.kind == "case_whitespace" and _is_text(f.type):
            if f.name != key and f.name not in fks:
                col = _col(t, t.column_names.index(f.name))
                if t.num_rows and len(pc.unique(col)) <= 50:
                    out.append(f.name)
        elif c.kind == "orphan_keys" and f.name in run.foreign_keys(table):
            out.append(f.name)
    return out


# ---- the corruptions --------------------------------------------------------------------------


def _duplicates(run: _Run, c: Corruption, pos: int, table: str) -> None:
    t = run.tables[table]
    n = t.num_rows
    rng = _rng(run.seed, run.batch, pos, table, None)
    source = _pick(rng, np.arange(n, dtype=np.int64), n, c.rate_in(run.batch))
    if len(source) == 0:
        return
    out = pa.concat_tables([t, t.take(pa.array(source))])
    run.tables[table] = out
    dest = np.arange(n, n + len(source), dtype=np.int64)
    run.record(table, c, dest, None, None, None, [{"source_row": int(s)} for s in source.tolist()])


def _orphan_keys(run: _Run, c: Corruption, pos: int, table: str, column: str) -> None:
    t = run.tables[table]
    i = t.column_names.index(column)
    col = _col(t, i)
    rng = _rng(run.seed, run.batch, pos, table, column)
    rows = _pick(rng, np.flatnonzero(_valid(col)), t.num_rows, c.rate_in(run.batch))
    if len(rows) == 0:
        return
    before = _cells(col, rows)
    if pa.types.is_integer(col.type) or pa.types.is_floating(col.type):
        peak = int(pc.max(col).as_py() or 0)
        base = max(ORPHAN_BASE, 2 * peak)
        ref = run.references.get(f"{table}.{column}")
        if ref:
            ptable, _, pcol = ref.partition(".")
            if ptable in run.tables and pcol in run.tables[ptable].column_names:
                parent_peak = pc.max(
                    _col(run.tables[ptable], run.tables[ptable].column_names.index(pcol))
                )
                base = max(base, int(parent_peak.as_py() or 0) + 1)
        orphans: list[Any] = [base + int(v) for v in rng.integers(0, 999_999, size=len(rows))]
        new = _write_orphans(col, rows, orphans)
    else:
        orphans = [f"ORPHAN-{int(v):09d}" for v in rng.integers(0, 999_999_999, size=len(rows))]
        cells = col.to_pylist()
        for r, v in zip(rows.tolist(), orphans, strict=True):
            cells[r] = v
        new = pa.array(cells, type=pa.string())
    run.tables[table] = _put(t, i, new)
    run.record(table, c, rows, column, before, _cells(new, rows))


def _date_shift(run: _Run, c: Corruption, pos: int, table: str, column: str) -> None:
    t = run.tables[table]
    i = t.column_names.index(column)
    col = _col(t, i)
    typ = col.type
    if not _is_date(typ):
        raise ValueError(f"date_shift: {table}.{column} is {typ}, not a date or timestamp")
    rng = _rng(run.seed, run.batch, pos, table, column)
    rows = _pick(rng, np.flatnonzero(_valid(col)), t.num_rows, c.rate_in(run.batch))
    if len(rows) == 0:
        return
    span = int(c.options.get("days", 7))
    direction = c.options.get("direction", "both")
    days = rng.integers(1, span + 1, size=len(rows))
    if direction == "both":
        days = days * rng.choice([-1, 1], size=len(rows))
    elif direction == "early":
        days = -days
    if pa.types.is_timestamp(typ):
        per_day, int_type = _UNIT_PER_DAY[typ.unit], pa.int64()
    elif pa.types.is_date32(typ):
        per_day, int_type = 1, pa.int32()
    else:  # date64 counts milliseconds
        per_day, int_type = _UNIT_PER_DAY["ms"], pa.int64()
    raw = np.array(col.cast(int_type).fill_null(0).to_numpy(zero_copy_only=False))
    raw[rows] += days.astype(raw.dtype) * per_day
    new = pa.array(raw, type=int_type, mask=~_valid(col)).cast(typ)
    before = _cells(col, rows)
    run.tables[table] = _put(t, i, new)
    run.record(
        table,
        c,
        rows,
        column,
        before,
        _cells(new, rows),
        [{"days": int(d)} for d in days.tolist()],
    )


def _negative_amounts(run: _Run, c: Corruption, pos: int, table: str, column: str) -> None:
    t = run.tables[table]
    i = t.column_names.index(column)
    col = _col(t, i)
    if not _is_amount(col.type):
        raise ValueError(f"negative_amounts: {table}.{column} is {col.type}, not a number")
    positive = np.array(
        pc.fill_null(pc.greater(col, 0), False).to_numpy(zero_copy_only=False), dtype=bool
    )
    rng = _rng(run.seed, run.batch, pos, table, column)
    rows = _pick(rng, np.flatnonzero(positive), t.num_rows, c.rate_in(run.batch))
    if len(rows) == 0:
        return
    flip = np.zeros(t.num_rows, dtype=bool)
    flip[rows] = True
    flipped = pc.if_else(pa.array(flip), pc.negate(col), col)
    before = _cells(col, rows)
    run.tables[table] = _put(t, i, flipped.cast(col.type))
    run.record(table, c, rows, column, before, _cells(flipped, rows))


def _vary(value: str, mode: int) -> str:
    if mode == 0:
        return value.upper()
    if mode == 1:
        return value.lower()
    if mode == 2:
        return value + " "
    return " " + value


def _case_whitespace(run: _Run, c: Corruption, pos: int, table: str, column: str) -> None:
    t = run.tables[table]
    i = t.column_names.index(column)
    col = _col(t, i)
    if not _is_text(col.type):
        raise ValueError(f"case_whitespace: {table}.{column} is {col.type}, not text")
    rng = _rng(run.seed, run.batch, pos, table, column)
    cells = col.to_pylist()
    # A row changes only when a variant differs from the value, so every record is a real change.
    modes = rng.integers(0, 4, size=len(cells))
    eligible = np.array(
        [r for r, v in enumerate(cells) if v is not None and _vary(v, int(modes[r])) != v],
        dtype=np.int64,
    )
    rows = _pick(rng, eligible, t.num_rows, c.rate_in(run.batch))
    if len(rows) == 0:
        return
    before = [cells[r] for r in rows.tolist()]
    for r in rows.tolist():
        cells[r] = _vary(cells[r], int(modes[r]))
    new = pa.array(cells, type=col.type)
    run.tables[table] = _put(t, i, new)
    run.record(table, c, rows, column, before, [cells[r] for r in rows.tolist()])


def _pii_value(kind: str, rng: np.random.Generator) -> str:
    if kind == "email":
        return f"user{int(rng.integers(0, 10**8)):08d}@example.com"
    if kind == "phone":
        return f"555-01{int(rng.integers(0, 100)):02d}"
    # Area numbers 900-999 are never issued, so these are SSN-shaped and belong to nobody.
    area, group, serial = rng.integers(900, 1000), rng.integers(1, 100), rng.integers(1, 10000)
    return f"{int(area)}-{int(group):02d}-{int(serial):04d}"


def _pii_fill(run: _Run, c: Corruption, pos: int, table: str, column: str) -> None:
    t = run.tables[table]
    i = t.column_names.index(column)
    col = _col(t, i)
    if not _is_text(col.type):
        raise ValueError(f"pii_fill: {table}.{column} is {col.type}, not text")
    rng = _rng(run.seed, run.batch, pos, table, column)
    rows = _pick(rng, np.arange(t.num_rows, dtype=np.int64), t.num_rows, c.rate_in(run.batch))
    if len(rows) == 0:
        return
    kind = str(c.options.get("pii", "ssn"))
    cells = col.to_pylist()
    before = [cells[r] for r in rows.tolist()]
    for r in rows.tolist():
        cells[r] = _pii_value(kind, rng)
    run.tables[table] = _put(t, i, pa.array(cells, type=col.type))
    run.record(
        table,
        c,
        rows,
        column,
        before,
        [cells[r] for r in rows.tolist()],
        [{"pii": kind}] * len(rows),
    )


def _type_change(run: _Run, c: Corruption, pos: int, table: str, column: str) -> None:
    t = run.tables[table]
    i = t.column_names.index(column)
    col = _col(t, i)
    if _is_text(col.type):
        raise ValueError(f"type_change: {table}.{column} is already text")
    new = pc.cast(col, pa.string())
    run.tables[table] = _put(t, i, new)
    run.records.append(
        {
            "record": "change",
            "seed": run.seed,
            "batch": run.batch,
            "table": table,
            "kind": "type_change",
            "scope": "column",
            "row": None,
            "key": None,
            "column": column,
            "before": str(col.type),
            "after": "string",
            "rows": int(t.num_rows),
        }
    )
    run.applied.append({"kind": c.kind, "table": table, "column": column, "rows": int(t.num_rows)})


def _null_creep(run: _Run, c: Corruption, pos: int, table: str, column: str) -> None:
    t = run.tables[table]
    i = t.column_names.index(column)
    col = _col(t, i)
    rng = _rng(run.seed, run.batch, pos, table, column)
    rows = _pick(rng, np.flatnonzero(_valid(col)), t.num_rows, c.rate_in(run.batch))
    if len(rows) == 0:
        return
    before = _cells(col, rows)
    keep = np.ones(t.num_rows, dtype=bool)
    keep[rows] = False
    new = col.take(pa.array(np.arange(t.num_rows), mask=~keep))
    run.tables[table] = _put(t, i, new)
    run.record(
        table,
        c,
        rows,
        column,
        before,
        [None] * len(rows),
        [{"rate": c.rate_in(run.batch)}] * len(rows),
    )


_COLUMN_KINDS = {
    "orphan_keys": _orphan_keys,
    "date_shift": _date_shift,
    "negative_amounts": _negative_amounts,
    "case_whitespace": _case_whitespace,
    "pii_fill": _pii_fill,
    "type_change": _type_change,
    "null_creep": _null_creep,
}


def corrupt_tables(
    tables: Mapping[str, pa.Table],
    corruptions: Sequence[Corruption],
    *,
    seed: int,
    batch: int = 0,
    keys: Mapping[str, str] | None = None,
    references: Mapping[str, str] | None = None,
) -> ChaosOutcome:
    """Apply ``corruptions`` in order to ``tables`` for batch ``batch``.

    Args:
        tables: The tables (Arrow); never modified.
        corruptions: The corruptions, applied in this order.
        seed: The seed; with the batch it fixes every draw.
        batch: The batch number (a day, for a daily run); selects the corruptions that are active
            and the rate of a null creep.
        keys: ``{"order": "order_id"}``, the key column of a table (default: its first column);
            the log names a row by it.
        references: ``{"order.customer_id": "customer.customer_id"}``, the foreign keys of the
            tables (default: ``*_id`` columns other than the key); orphan keys are chosen to match
            no parent row.

    Returns:
        A :class:`ChaosOutcome` with the corrupted tables and the log records.
    """
    run = _Run(tables, seed, batch, keys or {}, references or {})
    rows_in = {n: t.num_rows for n, t in tables.items()}
    seen: dict[tuple[str, str | None, str | None], int] = {}
    for c in corruptions:
        occurrence = seen.get((c.kind, c.table, c.column), 0)
        seen[(c.kind, c.table, c.column)] = occurrence + 1
        pos = zlib.crc32(f"{c.kind}:{occurrence}".encode())
        if not c.active(batch):
            continue
        for table in _targets(run, c):
            if c.kind == "duplicates":
                _duplicates(run, c, pos, table)
                continue
            columns = _columns(run, c, table)
            for column in columns:
                _COLUMN_KINDS[c.kind](run, c, pos, table, column)
    return ChaosOutcome(
        run.tables, run.records, run.applied, seed, batch, list(corruptions), rows_in
    )


def write_ground_truth(path: str | Path, outcome: ChaosOutcome) -> Path:
    """Write the log as JSON Lines: a ``run`` record, then one ``change`` record per change."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as f:
        f.write(json.dumps(outcome.header(), sort_keys=True, ensure_ascii=False) + "\n")
        for rec in outcome.records:
            f.write(json.dumps(rec, sort_keys=True, ensure_ascii=False) + "\n")
    return target


def read_ground_truth(path: str | Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """The ``run`` record and the ``change`` records of a :func:`write_ground_truth` log."""
    lines = [
        json.loads(x) for x in Path(path).read_text(encoding="utf-8").splitlines() if x.strip()
    ]
    if not lines or lines[0].get("record") != "run":
        raise ValueError(f"{path} is not a chaos ground-truth log (no run record first)")
    return lines[0], lines[1:]


__all__ = [
    "CORRUPTIONS",
    "ChaosOutcome",
    "Corruption",
    "corrupt_tables",
    "parse_corruptions",
    "read_ground_truth",
    "write_ground_truth",
]
