"""Arrow helpers for the file-drop, SCD2, stream and workflow simulators (P6-04a).

The simulators take generated tables as Arrow tables: a ``dict[str, pa.Table]``, a generation
result (its ``tables``), or pandas frames (converted when a frame is given; pandas stays
optional). Files are written through the ``shape.sinks`` plugins, so a file drop writes the
same Parquet, CSV and JSON Lines as ``shape generate``.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]

FILE_FORMATS = ("parquet", "csv", "jsonl")
"""The formats a drop can write (the sink names of the same files)."""

DATE_WORDS = ("date", "timestamp", "created", "updated")
"""A column named with one of these is a candidate time column when no column is a timestamp."""

CADENCES: dict[str, dt.timedelta] = {
    "daily": dt.timedelta(days=1),
    "hourly": dt.timedelta(hours=1),
    "every_15m": dt.timedelta(minutes=15),
}


def as_tables(source: Any) -> dict[str, pa.Table]:
    """``source`` as ``{name: Arrow table}``: a mapping of tables or frames, or an object with a
    ``tables`` mapping (a generation result)."""
    tables = (
        source.tables if hasattr(source, "tables") and not isinstance(source, Mapping) else source
    )
    if not isinstance(tables, Mapping):
        raise TypeError("expected a mapping of table name to table, or a generation result")
    return {str(name): as_table(table) for name, table in tables.items()}


def as_table(table: Any) -> pa.Table:
    """One Arrow table from an Arrow table, record batch or pandas frame."""
    if isinstance(table, pa.Table):
        return table
    if isinstance(table, pa.RecordBatch):
        return pa.Table.from_batches([table])
    if hasattr(table, "to_arrow") and callable(table.to_arrow):  # a generation table wrapper
        return as_table(table.to_arrow())
    if hasattr(table, "iloc"):  # a pandas frame
        return pa.Table.from_pandas(table, preserve_index=False)
    raise TypeError(f"cannot read {type(table).__name__} as a table")


def check_format(fmt: str) -> str:
    if fmt not in FILE_FORMATS:
        raise ValueError(f"Unsupported file format: {fmt!r} (use one of {', '.join(FILE_FORMATS)})")
    return fmt


def check_cadence(cadence: str) -> dt.timedelta:
    if cadence not in CADENCES:
        raise ValueError(f"unknown cadence {cadence!r}; use one of {', '.join(CADENCES)}")
    return CADENCES[cadence]


def parse_day(text: str, name: str) -> dt.datetime:
    """``YYYY-MM-DD`` as a datetime at midnight; the error names the setting."""
    try:
        return dt.datetime.strptime(text, "%Y-%m-%d")
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a date as YYYY-MM-DD, not {text!r}") from None


def write_table(table: pa.Table, path: Path, fmt: str) -> None:
    """Write ``table`` to ``path`` as ``fmt`` (parquet, csv or jsonl) with the ``shape.sinks``
    plugin of that name; the folder is created."""
    from shape.plugins.host import default_host

    check_format(fmt)
    path.parent.mkdir(parents=True, exist_ok=True)
    sink = default_host().get("shape.sinks", fmt)
    sink.write(str(path), path.stem, iter(table.to_batches()), schema=table.schema)


def is_temporal(t: pa.DataType) -> bool:
    return bool(pa.types.is_timestamp(t) or pa.types.is_date(t))


def to_timestamps(column: pa.ChunkedArray | pa.Array) -> pa.Array:
    """``column`` as naive microsecond timestamps (UTC wall time for a zoned type); a value that
    is not a date becomes null. Dates, timestamps and ISO-8601 strings are understood."""
    col = column.combine_chunks() if isinstance(column, pa.ChunkedArray) else column
    t = col.type
    if pa.types.is_dictionary(t):
        col = col.cast(t.value_type)
        t = col.type
    if pa.types.is_timestamp(t):
        return col.cast(pa.timestamp("us"), safe=False)
    if pa.types.is_date(t):
        return col.cast(pa.timestamp("ms")).cast(pa.timestamp("us"))
    if pa.types.is_string(t) or pa.types.is_large_string(t):
        try:
            return col.cast(pa.timestamp("us"))
        except pa.ArrowInvalid:
            values: list[dt.datetime | None] = []
            for v in col.to_pylist():
                try:
                    values.append(None if v is None else dt.datetime.fromisoformat(v))
                except ValueError:
                    values.append(None)
            return pa.array(values, pa.timestamp("us"))
    return pa.nulls(len(col), pa.timestamp("us"))


def temporal_column(table: pa.Table) -> str | None:
    """The column that says when a row happened: the first timestamp column; else the first
    column named like a date (``date``, ``timestamp``, ``created``, ``updated``) whose first
    values are dates, ISO-8601 text or date types. A number is not a date."""
    for f in table.schema:
        if pa.types.is_timestamp(f.type):
            return str(f.name)
    for f in table.schema:
        name = str(f.name).lower()
        if not any(w in name for w in DATE_WORDS):
            continue
        t = f.type
        if pa.types.is_date(t):
            return str(f.name)
        if pa.types.is_string(t) or pa.types.is_large_string(t):
            head = table.column(f.name).drop_null().slice(0, 5)
            if len(head) and to_timestamps(head).null_count == 0:
                return str(f.name)
    return None


def window_mask(stamps: pa.Array, start: dt.datetime, end: dt.datetime) -> pa.Array:
    """Rows with ``start <= time < end`` (a null time is outside every window)."""
    lo = pa.scalar(start, pa.timestamp("us"))
    hi = pa.scalar(end, pa.timestamp("us"))
    return pc.fill_null(pc.and_(pc.greater_equal(stamps, lo), pc.less(stamps, hi)), False)


def take_mask(table: pa.Table, mask: pa.Array | np.ndarray[Any, Any]) -> pa.Table:
    """The rows where ``mask`` is true, in table order."""
    return table.filter(mask if isinstance(mask, pa.Array) else pa.array(mask))


def concat(tables: list[pa.Table]) -> pa.Table:
    return pa.concat_tables(tables, promote_options="default")


def iso_utc_now() -> str:
    return dt.datetime.now(dt.UTC).isoformat()
