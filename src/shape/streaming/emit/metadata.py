"""Common per-message metadata for broker emitters (W9-08)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]

from shape.errors import ShapeError


def metadata(
    batch: pa.RecordBatch,
    *,
    key: str | None = None,
    headers: Mapping[str, Any] | Sequence[str] | None = None,
    timestamp: str = "broker",
    partition_key: str | None = None,
) -> list[tuple[bytes | None, list[tuple[str, bytes]], int | None, str | None]]:
    """Validate before sending, then resolve each row's metadata.

    Header column references use ``@column``; key columns are separated by ``|``.
    Null key components become empty strings. Timestamps are milliseconds.
    """
    if timestamp not in ("broker", "event_time"):
        raise ShapeError("timestamp must be 'broker' or 'event_time'")
    if isinstance(headers, str):
        headers = [headers]
    keys = [] if key in (None, "idempotency") else key.split("|")
    pairs = list(headers.items()) if isinstance(headers, Mapping) else []
    if headers is not None and not isinstance(headers, Mapping):
        for entry in headers:
            name, sep, value = entry.partition("=")
            if not sep:
                raise ShapeError("headers must use name=value or name=@column")
            pairs.append((name, value))
    refs = list(keys)
    if partition_key not in (None, "table", "none"):
        assert partition_key is not None
        refs.append(partition_key)
    for name, value in pairs:
        if not isinstance(name, str) or not name:
            raise ShapeError("header names must be nonempty strings")
        if name.lower().startswith(("shape-", "shape_")):
            raise ShapeError("shape-* header/property names are reserved")
        if isinstance(value, str) and value.startswith("@"):
            refs.append(value[1:])
    if timestamp == "event_time":
        refs.append("_shape_event_time")
        if "_shape_event_time" in batch.schema.names and not pa.types.is_timestamp(
            batch.schema.field("_shape_event_time").type
        ):
            raise ShapeError("event_time timestamp needs a timestamp column")
    missing = sorted(set(refs) - set(batch.schema.names))
    if missing:
        raise ShapeError(f"unknown metadata columns: {missing}")
    rows = batch.to_pylist()
    times = (
        batch.column("_shape_event_time")
        .cast(pa.timestamp("ms"), safe=False)
        .cast(pa.int64())
        .to_pylist()
        if timestamp == "event_time"
        else [None] * len(rows)
    )

    def text(v: Any) -> str:
        return "" if v is None else str(v)

    def binary(v: Any) -> bytes:
        return v if isinstance(v, bytes) else text(v).encode()

    return [
        (
            "|".join(text(row[c]) for c in keys).encode() if keys else None,
            [
                (
                    name,
                    binary(
                        row[value[1:]]
                        if isinstance(value, str) and value.startswith("@")
                        else value
                    ),
                )
                for name, value in pairs
            ],
            times[i],
            text(row[partition_key]) if partition_key not in (None, "table", "none") else None,
        )
        for i, row in enumerate(rows)
    ]
