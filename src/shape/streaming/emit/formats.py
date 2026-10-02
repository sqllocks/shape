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
from collections.abc import Iterator, Mapping
from typing import Any

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


def with_event_fields(batch: pa.RecordBatch, table: str, seq_start: int) -> pa.RecordBatch:
    """``batch`` (rows ``seq_start ..`` of ``table``) as flat events: the columns, then
    ``_shape_table``, ``_shape_seq`` and, if the table has one, ``_shape_event_time``."""
    clash = [n for n in _RESERVED if n in batch.schema.names]
    if clash:
        raise ShapeSchemaError(
            f"table {table!r} has a column named {clash[0]!r}, which is reserved"
        )
    n = batch.num_rows
    time_col = event_time_column(batch.schema)
    arrays = list(batch.columns)
    names = list(batch.schema.names)
    arrays.append(pa.array([table] * n, pa.string()))
    names.append(FIELD_TABLE)
    arrays.append(pa.array(range(seq_start, seq_start + n), pa.int64()))
    names.append(FIELD_SEQ)
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


def encode_batch(batch: pa.RecordBatch, envelope: str = "flat", source: str = "shape") -> bytes:
    """``batch`` of flat events as UTF-8 JSON lines, one per line, each ending in a newline."""
    if envelope not in ENVELOPES:
        raise ValueError(f"unknown envelope {envelope!r}; choose from {', '.join(ENVELOPES)}")
    if batch.num_rows == 0:
        return b""
    rows = rows_of(batch)
    if envelope == "cloudevents":
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
        rows = wrapped
    return ("\n".join(_dumps(r) for r in rows) + "\n").encode("utf-8")


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
