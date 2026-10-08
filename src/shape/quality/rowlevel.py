"""Row-level view of the validation gates: which rows fail, safe samples of them, a flag column.

The gates in :mod:`shape.quality.gates` count what fails. This module finds the rows, for the
gates where a failure belongs to rows (nulls, repeated keys, orphan foreign keys, values out of
range, dates out of range or in the future, a date that ends before it starts), with the same
rules the gates use. Nothing here writes data: the quarantine tables of
:mod:`shape.quality.quarantine` are untouched.

Samples are safe by default: a column that is classified (declared by the caller, named like
personal data, or holding values that look like it) shows ``[redacted]`` instead of its value.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]

from .gates import (
    ValidationContext,
    _comparable,
    _timestamp_scalar,
)

REDACTED = "[redacted]"
DEFAULT_FLAG_COLUMN = "_shape_dq_failed"
_MAX_TEXT = 64
_DETECT_SAMPLE = 1000

_NAME_HINT = re.compile(
    r"e_?mail|ssn|social_?security|phone|mobile|passport|birth|dob|iban|card_?(number|no)|"
    r"credit_?card|password|passwd|secret|token|address|first_?name|last_?name|full_?name|surname",
    re.IGNORECASE,
)


@dataclass(eq=False)
class CheckOutcome:
    """One check of one gate on one table (and columns): how many rows it looked at and which
    of them failed. ``failing_rows`` are 0-based row positions in ascending order."""

    gate: str
    table: str
    columns: tuple[str, ...]
    label: str
    rows: int
    failing_rows: np.ndarray[Any, np.dtype[np.int64]] = field(
        default_factory=lambda: np.zeros(0, dtype=np.int64)
    )

    @property
    def failing(self) -> int:
        return int(len(self.failing_rows))


def _outcome(
    gate: str, table: str, columns: tuple[str, ...], label: str, rows: int, mask: Any
) -> CheckOutcome:
    return CheckOutcome(gate, table, columns, label, rows, np.flatnonzero(mask).astype(np.int64))


def _to_mask(arr: Any) -> np.ndarray[Any, np.dtype[np.bool_]]:
    """A boolean Arrow array as a numpy mask with nulls as False."""
    if isinstance(arr, pa.ChunkedArray):
        arr = arr.combine_chunks()
    out: np.ndarray[Any, np.dtype[np.bool_]] = np.asarray(
        arr.fill_null(False).to_numpy(zero_copy_only=False), dtype=bool
    )
    return out


def _missing_mask(col: Any) -> np.ndarray[Any, np.dtype[np.bool_]]:
    """Nulls plus NaNs: what the null gate counts as missing."""
    mask = _to_mask(pc.is_null(col))
    if pa.types.is_floating(col.type):
        mask = mask | _to_mask(pc.is_nan(col))
    return mask


# -- one function per gate -----------------------------------------------------------------


def _nulls(ctx: ValidationContext) -> list[CheckOutcome]:
    out: list[CheckOutcome] = []
    if ctx.schema is None:
        return out
    for tname, tdef in ctx.schema.tables.items():
        table = ctx.tables.get(tname)
        if table is None:
            continue
        for cname, cdef in tdef.columns.items():
            if cname not in table.column_names or cdef.nullable:
                continue
            mask = _missing_mask(table.column(cname))
            out.append(_outcome("null_constraint", tname, (cname,), "null", table.num_rows, mask))
    return out


def _unique_name(table: pa.Table) -> str:
    name = "__row"
    while name in table.column_names:
        name += "_"
    return name


def _repeats(table: pa.Table, columns: list[str]) -> np.ndarray[Any, np.dtype[np.bool_]]:
    """Rows that repeat an earlier row on ``columns`` (nulls compare equal)."""
    n = table.num_rows
    idx = _unique_name(table)
    keyed = table.select(columns).append_column(idx, pa.array(np.arange(n, dtype=np.int64)))
    first = keyed.group_by(columns, use_threads=False).aggregate([(idx, "min")])
    mask = np.ones(n, dtype=bool)
    mask[np.asarray(first.column(f"{idx}_min").to_numpy(zero_copy_only=False), dtype=np.int64)] = (
        False
    )
    return mask


def _uniques(ctx: ValidationContext) -> list[CheckOutcome]:
    out: list[CheckOutcome] = []
    if ctx.schema is None:
        return out
    for tname, tdef in ctx.schema.tables.items():
        table = ctx.tables.get(tname)
        if table is None or not tdef.primary_key:
            continue
        pk = [c for c in tdef.primary_key if c in table.column_names]
        if not pk:
            continue
        out.append(
            _outcome(
                "unique_constraint",
                tname,
                tuple(pk),
                "duplicate",
                table.num_rows,
                _repeats(table, pk),
            )
        )
    return out


def _orphan_mask(child: Any, parent: Any) -> np.ndarray[Any, np.dtype[np.bool_]]:
    present = ~_missing_mask(child)
    if _comparable(child.type, parent.type):
        try:
            keys = pc.cast(parent, child.type).combine_chunks()
            found = _to_mask(pc.is_in(child, value_set=keys))
            return np.asarray(present & ~found, dtype=bool)
        except (pa.ArrowInvalid, pa.ArrowNotImplementedError, pa.ArrowTypeError):
            pass
    parents = set(parent.to_pylist())
    values = child.to_pylist()
    return np.array(
        [bool(p) and v not in parents for p, v in zip(present, values, strict=True)], dtype=bool
    )


def _references(ctx: ValidationContext) -> list[CheckOutcome]:
    out: list[CheckOutcome] = []
    if ctx.schema is None:
        return out
    for rel in ctx.schema.relationships:
        if rel.type == "self_referencing":
            continue
        parent = ctx.tables.get(rel.parent)
        child = ctx.tables.get(rel.child)
        if parent is None or child is None:
            continue
        for p_col, c_col in zip(rel.parent_columns, rel.child_columns, strict=False):
            if p_col not in parent.column_names or c_col not in child.column_names:
                continue
            mask = _orphan_mask(child.column(c_col), parent.column(p_col))
            out.append(
                _outcome(
                    "referential_integrity", rel.child, (c_col,), "orphan", child.num_rows, mask
                )
            )
    return out


def _ranges(ctx: ValidationContext) -> list[CheckOutcome]:
    out: list[CheckOutcome] = []
    for key, bounds in (ctx.config.get("ranges") or {}).items():
        parts = key.split(".", 1)
        if len(parts) != 2:
            continue
        tname, cname = parts
        table = ctx.tables.get(tname)
        if table is None or cname not in table.column_names:
            continue
        col = table.column(cname)
        t = col.type
        if not (pa.types.is_integer(t) or pa.types.is_floating(t) or pa.types.is_decimal(t)):
            continue
        values = np.asarray(
            pc.cast(col, pa.float64()).combine_chunks().to_numpy(zero_copy_only=False),
            dtype=np.float64,
        )
        with np.errstate(invalid="ignore"):
            mask = np.zeros(len(values), dtype=bool)
            if bounds.get("min") is not None:
                mask |= values < bounds["min"]
            if bounds.get("max") is not None:
                mask |= values > bounds["max"]
        out.append(
            _outcome("range_constraint", tname, (cname,), "out_of_range", table.num_rows, mask)
        )
    return out


def _temporal(ctx: ValidationContext) -> list[CheckOutcome]:
    out: list[CheckOutcome] = []
    config = ctx.config
    date_range = config.get("date_range") or {}
    if date_range:
        for tname, table in ctx.tables.items():
            for cname in table.column_names:
                col = table.column(cname)
                if not pa.types.is_timestamp(col.type):
                    continue
                mask = np.zeros(table.num_rows, dtype=bool)
                if "start" in date_range:
                    mask |= _to_mask(pc.less(col, _timestamp_scalar(date_range["start"], col.type)))
                if "end" in date_range:
                    mask |= _to_mask(
                        pc.greater(col, _timestamp_scalar(date_range["end"], col.type))
                    )
                out.append(
                    _outcome(
                        "temporal_consistency",
                        tname,
                        (cname,),
                        "out_of_range",
                        table.num_rows,
                        mask,
                    )
                )
    for spec in config.get("no_future") or []:
        parts = spec.split(".", 1)
        if len(parts) != 2:
            continue
        tname, cname = parts
        table = ctx.tables.get(tname)
        if table is None or cname not in table.column_names:
            continue
        col = table.column(cname)
        if not pa.types.is_timestamp(col.type):
            continue
        # a timestamp without a zone is UTC, so the answer does not depend on where this runs
        now = datetime.now(UTC) if col.type.tz else datetime.now(UTC).replace(tzinfo=None)
        mask = _to_mask(pc.greater(col, _timestamp_scalar(now, col.type)))
        out.append(
            _outcome("temporal_consistency", tname, (cname,), "future", table.num_rows, mask)
        )
    for rule in config.get("ordering") or []:
        tname = rule.get("table", "")
        start, end = rule.get("start", ""), rule.get("end", "")
        table = ctx.tables.get(tname)
        if table is None or start not in table.column_names or end not in table.column_names:
            continue
        try:
            mask = _to_mask(pc.less(table.column(end), table.column(start)))
        except (pa.ArrowInvalid, pa.ArrowNotImplementedError, pa.ArrowTypeError):
            continue
        out.append(
            _outcome(
                "temporal_consistency",
                tname,
                (start, end),
                "ends_before_start",
                table.num_rows,
                mask,
            )
        )
    return out


_EVALUATORS = {
    "null_constraint": _nulls,
    "unique_constraint": _uniques,
    "referential_integrity": _references,
    "range_constraint": _ranges,
    "temporal_consistency": _temporal,
}


def row_outcomes(gate: str, context: ValidationContext) -> list[CheckOutcome] | None:
    """The row-level checks of ``gate`` on ``context``, or None when the gate has no row-level
    form (schema conformance and drift, file format, distribution)."""
    fn = _EVALUATORS.get(gate)
    return fn(context) if fn else None


# -- classified columns and samples --------------------------------------------------------


def is_classified(
    table: str,
    column: str,
    data: pa.Table | None = None,
    declared: Mapping[str, Collection[str]] | None = None,
) -> bool:
    """True when ``column`` of ``table`` must not show its values: it is in ``declared``, its name
    reads as personal data, or a sample of its values matches a personal-data pattern."""
    if declared and column in declared.get(table, ()):
        return True
    if _NAME_HINT.search(column):
        return True
    if data is not None and column in data.column_names:
        from shape.privacy.detect import detect_column

        head = data.column(column).slice(0, _DETECT_SAMPLE).to_pylist()
        return bool(detect_column(head, _DETECT_SAMPLE))
    return False


def _show(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if np.isfinite(value) else str(value)
    text = str(value)
    return text if len(text) <= _MAX_TEXT else text[: _MAX_TEXT - 3] + "..."


def sample_failures(
    outcomes: list[CheckOutcome],
    tables: Mapping[str, pa.Table],
    *,
    limit: int = 5,
    classified: Mapping[str, Collection[str]] | None = None,
    show_classified: bool = False,
) -> list[dict[str, Any]]:
    """Up to ``limit`` failing rows per failing check, lowest row first. A sample names the gate,
    the table, the 0-based row, and the value of each column the check looked at; no other column
    of the row is shown. A classified column shows ``[redacted]`` unless ``show_classified``."""
    if limit < 0:
        raise ValueError("limit must be zero or more")
    samples: list[dict[str, Any]] = []
    if limit == 0:
        return samples
    for o in outcomes:
        if not o.failing:
            continue
        table = tables[o.table]
        hide = {
            c: not show_classified and is_classified(o.table, c, table, classified)
            for c in o.columns
        }
        for row in o.failing_rows[:limit]:
            values: dict[str, Any] = {}
            for c in o.columns:
                v = table.column(c)[int(row)].as_py()
                values[c] = REDACTED if hide[c] and v is not None else _show(v)
            samples.append(
                {
                    "gate": o.gate,
                    "check": o.label,
                    "table": o.table,
                    "row": int(row),
                    "values": values,
                    "redacted": any(v == REDACTED for v in values.values()),
                }
            )
    return samples


def flag_failing_rows(
    table: pa.Table,
    outcomes: list[CheckOutcome],
    table_name: str,
    name: str = DEFAULT_FLAG_COLUMN,
) -> pa.Table:
    """``table`` with a boolean column ``name``: true for every row that fails a check of
    ``outcomes`` on ``table_name``. The other columns are unchanged."""
    if name in table.column_names:
        raise ValueError(f"column {name!r} already exists in {table_name!r}; choose another name")
    mask = np.zeros(table.num_rows, dtype=bool)
    for o in outcomes:
        if o.table == table_name:
            mask[o.failing_rows] = True
    return table.append_column(name, pa.array(mask, pa.bool_()))
