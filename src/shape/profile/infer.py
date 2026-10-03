"""Type inference: the column type classification, and the Python-value rules for capture.

``infer_column_type`` classifies an Arrow column, using the pandas dtype the value would have after
``pandas.read_csv`` / ``read_parquet`` (the same emulation as the reference profiler in
``shape.profile.reference``): ``boolean``, ``integer``, ``float``, ``date``, ``datetime`` or
``string``. The internal parity harness checks it against the reference implementation on every
T-22 dataset.

``TypeTracker`` decides the type of a stream of Python values without ever discarding
evidence: it records every value as text and as a number side by side, so the final type can
be chosen at the end (bugs P2, P3 and P4).
"""

from __future__ import annotations

import decimal
import numbers
from dataclasses import dataclass
from typing import Any

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]

from shape.kernel.reference.exact import all_whole

from .reference.column import _all_parse_datetime, _coerce_datetime_strings, _first_is_iso
from .reference.readers import _arrow_cols, _csv_cols

_BOOL_WORDS = ["true", "false", "0", "1", "yes", "no"]
_PER_DAY = {"s": 86_400, "ms": 86_400_000, "us": 86_400_000_000, "ns": 86_400_000_000_000}


def _as_chunked(array: Any) -> Any:
    if isinstance(array, pa.ChunkedArray):
        return array
    if isinstance(array, pa.Array):
        return pa.chunked_array([array])
    return pa.chunked_array([pa.array(array)])


def _try_cast(arr: Any, typ: Any) -> bool:
    try:
        pc.cast(arr, typ)
    except (pa.ArrowInvalid, pa.ArrowNotImplementedError):
        return False
    return True


def infer_column_type(array: Any, source: str = "arrow") -> str:
    """The type name of one column: boolean, integer, float, date, datetime or string.

    ``source`` is ``"csv"`` when the column was parsed from text without pandas's date
    parsing (dates stay text, as in ``pandas.read_csv``), else ``"arrow"`` (Parquet, IPC,
    tables: ``Table.to_pandas()`` semantics).
    """
    if source not in ("csv", "arrow"):
        raise ValueError("source must be 'csv' or 'arrow'")
    col = _as_chunked(array)
    table = pa.table({"x": col})
    info = (_csv_cols(table) if source == "csv" else _arrow_cols(table))[0]
    kind, data = info.kind, info.arr
    non_null = pc.drop_null(data)
    n = len(non_null)
    if kind in ("bool", "objbool"):
        return "boolean"
    if kind in ("int", "uint64", "objint"):
        return "integer"
    if kind in ("cat", "objtime", "objbin", "objdur", "objmix"):
        return "string"  # categoricals and object columns of times/bytes/timedeltas/mixed values
    if kind == "objdec":
        if not n:
            return "string"
        values = [float(v) for v in non_null.to_pylist()]
        if {str(v).lower() for v in non_null.to_pylist()} <= set(_BOOL_WORDS):
            return "boolean"
        return "integer" if all(v == int(v) for v in values) else "float"
    if kind == "float":
        if n:
            vals = non_null.to_numpy()
            vals = vals[~np.isnan(vals)] if vals.dtype.kind == "f" else vals
            if len(vals) and all_whole(vals):
                return "integer"
        return "float"
    if kind == "dt64":
        if not n:
            return "datetime"
        per_day = _PER_DAY[non_null.type.unit]
        ints = pc.cast(non_null, pa.int64()).to_numpy()
        return "datetime" if np.any(ints % per_day) else "date"
    if kind == "objdate":
        return "datetime" if n else "string"
    if kind == "nullobj" or n == 0:
        return "string"
    uniq = pc.unique(non_null)
    lower = pc.utf8_lower(uniq)
    if pc.all(pc.is_in(lower, value_set=pa.array(_BOOL_WORDS))).as_py():
        return "boolean"
    if _try_cast(uniq, pa.float64()):
        u = pc.cast(uniq, pa.float64()).to_numpy()
        return "integer" if all_whole(u) else "float"
    # the profiler's decision, in its order (#336): ISO text is parsed in bulk; anything else is
    # checked on its distinct values, stopping at the first that does not parse, so ordinary
    # text costs one failed parse rather than one per distinct value
    if _first_is_iso(non_null):
        parsed = _coerce_datetime_strings(non_null, keep_nulls=True)
        if parsed is not None and parsed.null_count == 0:
            return "datetime"
    return "datetime" if _all_parse_datetime(uniq) else "string"


def is_number(v: Any) -> bool:
    """Numeric Python value: int, float, numpy integers and floats, ``Decimal``. Not bool."""
    if isinstance(v, (bool, np.bool_)):
        return False
    return isinstance(v, (numbers.Real, decimal.Decimal))


def as_number(v: Any) -> int | float:
    """The Python ``int``/``float`` for a numeric value (``Decimal`` becomes float)."""
    if isinstance(v, (int, np.integer)):
        return int(v)
    return float(v)


@dataclass
class TypeTracker:
    """Track whether every non-null value of a column has been numeric so far."""

    count: int = 0
    null_count: int = 0
    numeric_count: int = 0
    other_count: int = 0

    def observe(self, v: Any) -> None:
        self.count += 1
        if v is None:
            self.null_count += 1
        elif is_number(v):
            self.numeric_count += 1
        else:
            self.other_count += 1

    @property
    def kind(self) -> str:
        """``numeric`` when there is at least one number and nothing else (nulls do not
        count), otherwise ``text`` (including a column that is all null)."""
        return "numeric" if self.numeric_count and not self.other_count else "text"
