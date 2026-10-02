"""Event formats (D-12): the flat row (default) and the CloudEvents envelope (P5-01).

A *flat* event is the row's columns plus ``_shape_table``, ``_shape_seq`` and, when the table has
a date or timestamp column, ``_shape_event_time`` (that column's first occurrence, in column
order). The idempotency key of an event is ``(_shape_table, _shape_seq)``: ``_shape_seq`` is the
row's position in its table, so a replay produces the same key for the same row.

The *cloudevents* envelope wraps the flat event as the ``data`` of a CloudEvents 1.0 structured
JSON event: ``id`` is ``<table>/<seq>``, ``type`` is ``shape.<table>.row``, ``time`` is the event
time when there is one, and ``shapetable`` and ``shapeseq`` are extension attributes (CloudEvents
names are lower-case letters and digits).

JSON values: timestamps and dates are ISO-8601 strings (UTC ``Z`` when the type has a time zone),
decimals are strings (no precision loss), binary is base64, non-finite floats are ``null``.
"""

from __future__ import annotations

import base64
import json
import os
from collections.abc import Iterator, Mapping
from concurrent.futures import ThreadPoolExecutor
from typing import Any, NamedTuple

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]

from shape.errors import ShapeSchemaError

FIELD_TABLE = "_shape_table"
FIELD_SEQ = "_shape_seq"
FIELD_TIME = "_shape_event_time"
ENVELOPES = ("flat", "cloudevents")
_RESERVED = (FIELD_TABLE, FIELD_SEQ, FIELD_TIME)


def event_time_column(schema: pa.Schema) -> str | None:
    """The first date or timestamp column, or ``None``."""
    for f in schema:
        if pa.types.is_timestamp(f.type) or pa.types.is_date(f.type):
            return str(f.name)
    return None


def check_reserved(schema: pa.Schema, table: str) -> None:
    """Refuse a table that has a column named like an event field."""
    clash = [n for n in _RESERVED if n in schema.names]
    if clash:
        raise ShapeSchemaError(
            f"table {table!r} has a column named {clash[0]!r}, which is reserved"
        )


def event_columns(table: str, seq: pa.Array) -> tuple[pa.Array, pa.Array]:
    """The ``_shape_table`` and ``_shape_seq`` columns of events with row positions ``seq``."""
    return pa.repeat(table, len(seq)).cast(pa.string()), seq.cast(pa.int64())


def with_event_fields(batch: pa.RecordBatch, table: str, seq_start: int) -> pa.RecordBatch:
    """``batch`` (rows ``seq_start ..`` of ``table``) as flat events: the columns, then
    ``_shape_table``, ``_shape_seq`` and, if the table has one, ``_shape_event_time``."""
    check_reserved(batch.schema, table)
    n = batch.num_rows
    time_col = event_time_column(batch.schema)
    arrays = list(batch.columns)
    names = list(batch.schema.names)
    table_col, seq_col = event_columns(table, pa.array(np.arange(seq_start, seq_start + n)))
    arrays += [table_col, seq_col]
    names += [FIELD_TABLE, FIELD_SEQ]
    if time_col is not None:
        arrays.append(batch.column(time_col))
        names.append(FIELD_TIME)
    return pa.RecordBatch.from_arrays(arrays, names=names)


def _json_column(col: pa.ChunkedArray | pa.Array) -> list[Any]:
    t = col.type
    if pa.types.is_dictionary(t):
        col = col.cast(t.value_type)
        t = col.type
    if pa.types.is_floating(t):
        col = pc.if_else(pc.is_finite(col), col, pa.scalar(None, t))
    elif pa.types.is_timestamp(t):
        if t.tz is not None:
            col = col.cast(pa.timestamp(t.unit, "UTC"))
            col = pc.strftime(col, format="%Y-%m-%dT%H:%M:%SZ")
        else:
            col = pc.strftime(col, format="%Y-%m-%dT%H:%M:%S")
    elif pa.types.is_date(t) or pa.types.is_decimal(t) or pa.types.is_time(t):
        col = col.cast(pa.string())
    elif pa.types.is_binary(t) or pa.types.is_large_binary(t) or pa.types.is_fixed_size_binary(t):
        return [None if v is None else base64.b64encode(v).decode("ascii") for v in col.to_pylist()]
    return list(col.to_pylist())


def rows_of(batch: pa.RecordBatch) -> list[dict[str, Any]]:
    """The JSON-safe rows of ``batch`` (see the module docstring for the value rules)."""
    names = batch.schema.names
    columns = [_json_column(batch.column(i)) for i in range(batch.num_columns)]
    return [dict(zip(names, values, strict=True)) for values in zip(*columns, strict=True)]


def _dumps(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False, allow_nan=False, default=str)


def _wrap(rows: list[dict[str, Any]], envelope: str, source: str) -> list[dict[str, Any]]:
    if envelope not in ENVELOPES:
        raise ValueError(f"unknown envelope {envelope!r}; choose from {', '.join(ENVELOPES)}")
    if envelope == "flat":
        return rows
    wrapped = []
    for row in rows:
        table, seq = row[FIELD_TABLE], row[FIELD_SEQ]
        ce: dict[str, Any] = {
            "specversion": "1.0",
            "id": f"{table}/{seq}",
            "source": f"shape://{source}",
            "type": f"shape.{table}.row",
        }
        if row.get(FIELD_TIME) is not None:
            ce["time"] = row[FIELD_TIME]
        ce["datacontenttype"] = "application/json"
        ce["shapetable"] = table
        ce["shapeseq"] = seq
        ce["data"] = row
        wrapped.append(ce)
    return wrapped


def _encode_batch_rows(batch: pa.RecordBatch, envelope: str, source: str) -> bytes:
    """The row-by-row encoder: the reference for :func:`encode_batch`'s vectorised path, and the
    CloudEvents path."""
    rows = _wrap(rows_of(batch), envelope, source)
    return ("\n".join(_dumps(r) for r in rows) + "\n").encode("utf-8")


_NEEDS_ESCAPE = r'[\x00-\x1f"\\]'
_NULL = pa.scalar("null", pa.string())
# Python writes a float with repr(): positional between 1e-4 and 1e16. Arrow's cast to text gives
# the same shortest round-trip digits when it too writes the value positionally, except that repr
# adds ".0"; a column with a value outside that range, or that Arrow writes with an exponent, is
# written value by value.
_FLOAT_MIN, _FLOAT_MAX = 1e-4, 1e16


def _tokens_of_strings(values: list[Any]) -> pa.Array:
    return pa.array([_dumps(v) for v in values], pa.string())


def _filled(tokens: pa.Array) -> pa.Array:
    """``tokens`` with ``null`` for a null value."""
    return pc.fill_null(tokens, _NULL) if tokens.null_count else tokens


def _quote(col: pa.Array) -> pa.Array:
    """The JSON text of a string column (a null is ``null``)."""
    quoted = pc.binary_join_element_wise(
        pa.scalar('"'), col.cast(pa.string()), pa.scalar('"'), pa.scalar("")
    )
    return _filled(quoted)


def _column_tokens(col: pa.Array) -> pa.Array:
    """The JSON text of each value of ``col`` as a non-null string array, identical to
    ``_dumps`` of the value :func:`_json_column` gives, computed with Arrow kernels where that is
    cheap and exact, value by value where it is not."""
    t = col.type
    if pa.types.is_dictionary(t):
        col = col.cast(t.value_type)
        t = col.type
    if pa.types.is_null(t):
        return pa.array(["null"] * len(col), pa.string())
    if pa.types.is_boolean(t):
        return _filled(pc.if_else(col, "true", "false"))
    if pa.types.is_integer(t):
        return _filled(col.cast(pa.string()))
    if pa.types.is_float64(t):
        finite = pc.is_finite(col)
        col = pc.if_else(finite, col, pa.scalar(None, t))
        mag = pc.abs(col)
        out_of_range = pc.any(
            pc.and_(
                pc.not_equal(mag, 0.0),
                pc.or_(pc.less(mag, _FLOAT_MIN), pc.greater_equal(mag, _FLOAT_MAX)),
            )
        ).as_py()
        text = col.cast(pa.string())
        if not out_of_range and not pc.any(pc.match_substring(text, "e")).as_py():
            bare = pc.invert(pc.match_substring_regex(text, r"[.eE]"))
            text = pc.if_else(
                bare, pc.binary_join_element_wise(text, pa.scalar(".0"), pa.scalar("")), text
            )
            return _filled(text)
    elif pa.types.is_string(t) or pa.types.is_large_string(t):
        if not pc.any(pc.match_substring_regex(col, _NEEDS_ESCAPE)).as_py():
            return _quote(col)
    elif pa.types.is_timestamp(t):
        if t.tz is not None:
            col = col.cast(pa.timestamp(t.unit, "UTC"))
            return _quote(pc.strftime(col, format="%Y-%m-%dT%H:%M:%SZ"))
        # The text cast writes the same digits as ``%S`` (the fraction to the column's unit).
        iso = pc.replace_substring(col.cast(pa.string()), " ", "T", max_replacements=1)
        return _quote(iso)
    elif pa.types.is_date(t) or pa.types.is_decimal(t):
        return _quote(col.cast(pa.string()))
    return _tokens_of_strings(_json_column(col))


def _encode_flat(batch: pa.RecordBatch) -> bytes:
    """Flat events as JSON lines, one Arrow expression per column instead of one Python call per
    value (the same bytes as :func:`_encode_batch_rows`)."""
    parts: list[Any] = []
    done: dict[tuple[Any, ...], pa.Array] = {}  # the event time repeats a column
    for i, name in enumerate(batch.schema.names):
        col = batch.column(i)
        where = (
            col.type,
            col.offset,
            len(col),
            tuple(None if b is None else b.address for b in col.buffers()),
        )
        if where not in done:
            done[where] = _column_tokens(col)
        parts.append(pa.scalar(("{" if i == 0 else ",") + _dumps(name) + ":"))
        parts.append(done[where])
    parts.append(pa.scalar("}\n"))
    parts.append(pa.scalar(""))
    lines = pc.binary_join_element_wise(*parts)
    # The lines are contiguous in the value buffer: the bytes between the first and last offset.
    offsets = np.frombuffer(lines.buffers()[1], dtype=np.int32)
    first, last = int(offsets[lines.offset]), int(offsets[lines.offset + len(lines)])
    return bytes(memoryview(lines.buffers()[2])[first:last])


PARALLEL_ROWS = 16384  # a batch of at least this many events is encoded on several threads
_pool: ThreadPoolExecutor | None = None


def _encode_parallel(batch: pa.RecordBatch) -> bytes:
    """:func:`_encode_flat` of slices of ``batch`` on a thread pool (Arrow's kernels release the
    interpreter lock), joined in order."""
    global _pool
    if _pool is None:
        _pool = ThreadPoolExecutor(max_workers=max(1, min(4, os.cpu_count() or 1)))
    parts = _pool._max_workers
    step = -(-batch.num_rows // parts)
    futures = [
        _pool.submit(_encode_flat, batch.slice(i, step)) for i in range(0, batch.num_rows, step)
    ]
    return b"".join(f.result() for f in futures)


def encode_batch(batch: pa.RecordBatch, envelope: str = "flat", source: str = "shape") -> bytes:
    """``batch`` of flat events as UTF-8 JSON lines, one per line, each ending in a newline."""
    if envelope not in ENVELOPES:
        raise ValueError(f"unknown envelope {envelope!r}; choose from {', '.join(ENVELOPES)}")
    if batch.num_rows == 0:
        return b""
    if envelope == "flat":
        if batch.num_rows >= PARALLEL_ROWS:
            return _encode_parallel(batch)
        return _encode_flat(batch)
    return _encode_batch_rows(batch, envelope, source)


class EncodedEvent(NamedTuple):
    """One event ready for a message transport: the D-12 idempotency key as a string
    (``<table>/<seq>``, the CloudEvents ``id``), its parts, and the JSON body (no newline)."""

    key: str
    table: str
    seq: int
    time: str | None
    body: bytes


def encode_events(
    batch: pa.RecordBatch, envelope: str = "flat", source: str = "shape"
) -> list[EncodedEvent]:
    """``batch`` of flat events as one :class:`EncodedEvent` per row, for emitters that send one
    message per event (the same JSON as :func:`encode_batch`, line by line)."""
    if envelope not in ENVELOPES:
        raise ValueError(f"unknown envelope {envelope!r}; choose from {', '.join(ENVELOPES)}")
    flat = rows_of(batch)
    wrapped = _wrap(flat, envelope, source)
    return [
        EncodedEvent(
            f"{row[FIELD_TABLE]}/{row[FIELD_SEQ]}",
            row[FIELD_TABLE],
            row[FIELD_SEQ],
            row.get(FIELD_TIME),
            _dumps(out).encode("utf-8"),
        )
        for row, out in zip(flat, wrapped, strict=True)
    ]


def decode_line(line: str | bytes) -> dict[str, Any]:
    """One encoded event (either envelope) back to its flat event."""
    obj = json.loads(line)
    if isinstance(obj, dict) and obj.get("specversion") == "1.0" and "data" in obj:
        data = obj["data"]
        if isinstance(data, dict):
            return data
    if not isinstance(obj, dict):
        raise ValueError("an event is a JSON object")
    return obj


def event_key(event: Mapping[str, Any]) -> tuple[str, int]:
    """The idempotency key ``(_shape_table, _shape_seq)`` of a flat event."""
    return (event[FIELD_TABLE], event[FIELD_SEQ])


def read_events(path: str, *, dedupe: bool = False) -> Iterator[dict[str, Any]]:
    """The events of a JSON-lines file, in file order. With ``dedupe`` a repeat of a key is
    dropped (the first one wins), which is how a consumer collapses at-least-once delivery."""
    seen: set[tuple[str, int]] = set()
    with open(path, "rb") as f:
        for line in f:
            if not line.strip():
                continue
            event = decode_line(line)
            if dedupe:
                key = event_key(event)
                if key in seen:
                    continue
                seen.add(key)
            yield event
