"""Shared helpers of the pattern simulators (clickstream, financial, IoT, operational log, pulse).

The simulators work on Arrow tables (inputs and outputs) with numpy, so the plugin needs nothing
beyond Shape's own dependencies. Everything random comes from one ``numpy`` generator seeded by
the configuration, ids included: the same configuration and seed give the same tables.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]

FORMATS = ("parquet", "csv", "jsonl")


# ---- ids ------------------------------------------------------------------------------------


def uuid_strings(rng: np.random.Generator, n: int) -> list[str]:
    """``n`` version-4 style UUID strings drawn from ``rng`` (so a seed reproduces them)."""
    if n <= 0:
        return []
    raw = rng.integers(0, 256, size=(n, 16), dtype=np.uint8)
    raw[:, 6] = (raw[:, 6] & 0x0F) | 0x40
    raw[:, 8] = (raw[:, 8] & 0x3F) | 0x80
    hexa = raw.tobytes().hex()
    return [
        f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"
        for h in (hexa[i * 32 : (i + 1) * 32] for i in range(n))
    ]


def hex_strings(rng: np.random.Generator, n: int, length: int) -> list[str]:
    """``n`` random lowercase hex strings of ``length`` characters."""
    if n <= 0:
        return []
    nbytes = (length + 1) // 2
    raw = rng.integers(0, 256, size=(n, nbytes), dtype=np.uint8).tobytes().hex()
    width = nbytes * 2
    return [raw[i * width : i * width + length] for i in range(n)]


# ---- weighted draws -------------------------------------------------------------------------


def pick(
    rng: np.random.Generator,
    values: list[Any] | np.ndarray,
    size: int,
    p: list[float] | np.ndarray | None = None,
) -> np.ndarray:
    """``size`` draws from ``values`` (uniform, or with probabilities ``p``, normalised)."""
    if p is not None:
        weights = np.asarray(p, dtype=np.float64)
        weights = weights / weights.sum()
        return np.asarray(values)[rng.choice(len(values), size=size, p=weights)]
    return np.asarray(values)[rng.integers(0, len(values), size=size)]


# ---- Arrow in -------------------------------------------------------------------------------


def as_table(obj: Any) -> pa.Table:
    """An Arrow table from a table, a record batch, a mapping of columns or a pandas frame."""
    if isinstance(obj, pa.Table):
        return obj
    if isinstance(obj, pa.RecordBatch):
        return pa.Table.from_batches([obj])
    if isinstance(obj, Mapping):
        return pa.table(dict(obj))
    to_arrow = getattr(obj, "to_arrow", None)
    if callable(to_arrow):
        out = to_arrow()
        if isinstance(out, pa.Table):
            return out
    if hasattr(obj, "__arrow_c_stream__"):
        return pa.table(obj)
    if hasattr(obj, "columns") and hasattr(obj, "dtypes"):  # a pandas frame
        return pa.Table.from_pandas(obj, preserve_index=False)
    raise TypeError(f"cannot read {type(obj).__name__} as an Arrow table")


def table_mapping(tables: Any) -> dict[str, pa.Table]:
    """``tables`` (a mapping of tables, or a generation result with ``.tables``) as a dict."""
    inner = getattr(tables, "tables", tables)
    if not isinstance(inner, Mapping):
        raise TypeError("tables must map table names to tables")
    return {str(k): as_table(v) for k, v in inner.items()}


def timestamp_us(col: pa.ChunkedArray | pa.Array) -> tuple[np.ndarray, np.ndarray, str | None]:
    """A date, timestamp or ISO-8601 string column as ``(microseconds, valid, timezone)``.

    ``microseconds`` is int64 since the epoch (0 where null), ``valid`` marks the non-null
    cells and ``timezone`` is the column's time zone, if it had one.
    """
    if isinstance(col, pa.ChunkedArray):
        col = col.combine_chunks() if col.num_chunks else pa.array([], type=col.type)
    tz: str | None = None
    if pa.types.is_timestamp(col.type):
        tz = col.type.tz
        col = col.cast(pa.timestamp("us", tz))
    elif pa.types.is_date(col.type):
        col = col.cast(pa.timestamp("us"))
    elif pa.types.is_string(col.type) or pa.types.is_large_string(col.type):
        col = _text_times(col)
    else:
        raise TypeError(f"{col.type} is not a date or timestamp type")
    valid = ~np.asarray(col.is_null().to_numpy(zero_copy_only=False), dtype=bool)
    ints = col.cast(pa.int64()).fill_null(0).to_numpy(zero_copy_only=False)
    return ints.astype(np.int64), valid, tz


def _text_times(col: pa.Array) -> pa.Array:
    """ISO-8601 text as naive ``timestamp[us]``: a time with ``Z`` or an offset becomes its UTC
    wall time, one without is taken as it is. Text that is not a time raises ``ValueError``."""
    try:
        return pc.cast(col, pa.timestamp("us"))
    except pa.ArrowInvalid:
        pass
    try:  # every time carries a zone
        return pc.cast(col, pa.timestamp("us", "UTC")).cast(pa.timestamp("us"))
    except pa.ArrowInvalid:
        pass
    values: list[datetime | None] = []
    for text in col.to_pylist():  # some with a zone, some without
        if text is None:
            values.append(None)
            continue
        try:
            value = datetime.fromisoformat(text)
        except ValueError:
            raise ValueError(f"{text!r} is not an ISO-8601 date or time") from None
        if value.tzinfo is not None:
            value = value.astimezone(UTC).replace(tzinfo=None)
        values.append(value)
    return pa.array(values, pa.timestamp("us"))


def timestamps(us: np.ndarray, tz: str | None = None, valid: np.ndarray | None = None) -> pa.Array:
    """A ``timestamp[us]`` array (``tz`` if given) from microseconds, null where not ``valid``."""
    mask = None if valid is None else ~np.asarray(valid, dtype=bool)
    return pa.array(
        np.asarray(us, dtype=np.int64).astype("datetime64[us]"),
        type=pa.timestamp("us", tz),
        mask=mask,
    )


def parse_start(text: str) -> int:
    """An ISO-8601 instant as microseconds since the epoch (a missing zone means UTC)."""
    moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return int(moment.timestamp() * 1_000_000)


def float_values(col: pa.ChunkedArray | pa.Array) -> tuple[np.ndarray, np.ndarray]:
    """A numeric column as ``(float64 values, valid)``; null cells are NaN."""
    arr = pc.cast(col, pa.float64())
    if isinstance(arr, pa.ChunkedArray):
        arr = arr.combine_chunks() if arr.num_chunks else pa.array([], type=pa.float64())
    valid = ~np.asarray(arr.is_null().to_numpy(zero_copy_only=False), dtype=bool)
    values = arr.fill_null(float("nan")).to_numpy(zero_copy_only=False).astype(np.float64)
    return values, valid


def float_array(values: np.ndarray) -> pa.Array:
    """A float64 Arrow array from ``values``, null where the value is NaN."""
    values = np.asarray(values, dtype=np.float64)
    return pa.array(values, type=pa.float64(), mask=np.isnan(values))


def empty_table(schema: pa.Schema) -> pa.Table:
    return schema.empty_table()


def combine(tables: list[pa.Table]) -> pa.Table:
    """The rows of ``tables`` stacked, with the union of their columns (missing cells are null).

    Where the same column has different types (decimal and float amounts, say) numeric
    columns become float64 and anything else text.
    """
    names: list[str] = []
    types: dict[str, pa.DataType] = {}
    for table in tables:
        for field in table.schema:
            if field.name not in types:
                names.append(field.name)
                types[field.name] = field.type
            elif types[field.name] != field.type:
                a, b = types[field.name], field.type
                numeric = all(
                    pa.types.is_integer(t) or pa.types.is_floating(t) or pa.types.is_decimal(t)
                    for t in (a, b)
                )
                types[field.name] = pa.float64() if numeric else pa.string()
    schema = pa.schema([pa.field(n, types[n]) for n in names])
    parts = []
    for table in tables:
        cols = []
        for name in names:
            if name in table.column_names:
                cols.append(pc.cast(table.column(name), types[name]))
            else:
                cols.append(pa.nulls(table.num_rows, type=types[name]))
        parts.append(pa.Table.from_arrays(cols, schema=schema))
    return pa.concat_tables(parts)


def value_counts(values: np.ndarray) -> dict[str, int]:
    """``{value: count}`` ordered by count (largest first; ties by first appearance)."""
    if len(values) == 0:
        return {}
    uniq, first, counts = np.unique(values, return_index=True, return_counts=True)
    order = np.lexsort((first, -counts))
    return {str(uniq[i]): int(counts[i]) for i in order}


# ---- results --------------------------------------------------------------------------------


class TablesResult:
    """What every simulator's result offers: its tables, as flat events, and on disk."""

    TABLES: ClassVar[tuple[str, ...]] = ()
    stats: dict[str, Any]

    def table_map(self) -> dict[str, pa.Table]:
        """Every output table by name."""
        return {name: getattr(self, name) for name in self.TABLES}

    def events(self, table: str, *, seq_start: int = 0) -> pa.RecordBatch:
        """The rows of ``table`` as flat stream events: its columns plus ``_shape_table``,
        ``_shape_seq`` and (when it has a date or timestamp column) ``_shape_event_time``.
        Feed them to ``shape.streaming.emit`` sinks or an emitter."""
        from shape.streaming.emit.formats import with_event_fields

        source = self.table_map().get(table)
        if source is None:
            raise KeyError(f"no table {table!r}; the result has {', '.join(self.table_map())}")
        batch = source.combine_chunks().to_batches()
        merged = batch[0] if batch else pa.RecordBatch.from_pylist([], schema=source.schema)
        events: pa.RecordBatch = with_event_fields(merged, table, seq_start)
        return events

    def write(self, directory: str | Path, fmt: str = "parquet") -> dict[str, Path]:
        """Write every table to ``directory`` as ``<table>.<fmt>`` (parquet, csv or jsonl)
        and return the paths."""
        if fmt not in FORMATS:
            raise ValueError(f"unknown format {fmt!r}; choose from {', '.join(FORMATS)}")
        from shape.builtins.sinks.files import CsvSink, JsonlSink, ParquetSink

        sink: Any = {"parquet": ParquetSink, "csv": CsvSink, "jsonl": JsonlSink}[fmt]()
        out = Path(directory)
        out.mkdir(parents=True, exist_ok=True)
        written: dict[str, Path] = {}
        for name, table in self.table_map().items():
            sink.write(str(out) + "/", name, table.to_batches(), schema=table.schema)
            written[name] = out / f"{name}.{fmt}"
        (out / "stats.json").write_text(json.dumps(self.stats, indent=2, default=str) + "\n")
        return written
