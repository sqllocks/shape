"""Canonical opaque Arrow values shared by batch and streaming profiling."""

from __future__ import annotations

import datetime
import decimal
import json
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]

from shape.kernel.dispatch import get_kernel


def _scalar(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"$binary": value.hex()}
    if isinstance(value, (datetime.date, datetime.time, datetime.timedelta, decimal.Decimal)):
        return {"$" + type(value).__name__: str(value)}
    raise TypeError(f"cannot serialize nested scalar {type(value).__name__}")


def canonical_values(arr: Any) -> list[str | None]:
    """Sorted-key, ASCII JSON; Arrow maps stay ordered pairs, including duplicate keys.

    Arrow's default map conversion produces pairs rather than a lossy Python dictionary.
    ASCII escaping makes the string kernel's length equal the UTF-8 byte size.
    """
    return [
        None
        if value is None
        else json.dumps(value, sort_keys=True, separators=(",", ":"), default=_scalar)
        for value in arr.to_pylist()
    ]


def size_stats(values: list[str | None]) -> dict[str, Any]:
    lengths = [len(v) for v in values if v is not None]
    count = len(lengths)
    return {
        "format": "shape-nested-size",
        "version": 1,
        "unit": "utf8-bytes",
        "count": count,
        "total": sum(lengths),
        "min": min(lengths) if count else None,
        "max": max(lengths) if count else None,
        "mean": sum(lengths) / count if count else None,
    }


def wire_schema(schema: Any) -> Any:
    """Canonical text slots for nested fields; ordinary schemas pass through unchanged."""
    if not any(pa.types.is_nested(f.type) for f in schema):
        return schema
    return pa.schema(
        [
            pa.field(f.name, pa.string(), nullable=f.nullable, metadata=f.metadata)
            if pa.types.is_nested(f.type)
            else f
            for f in schema
        ],
        metadata=schema.metadata,
    )


def profile_state(schema: Any, mode: str) -> Any:
    """Keep the original kernel and allocation path for tables without nested fields."""
    if any(pa.types.is_nested(f.type) for f in schema):
        return ProfileState(schema, mode)
    return get_kernel().ProfileState(schema, mode)


def restore_state(schema: Any, raw: bytes) -> Any:
    if any(pa.types.is_nested(f.type) for f in schema):
        return ProfileState.from_snapshot(schema, raw)
    return get_kernel().ProfileState.from_snapshot(schema, raw)


class ProfileState:
    """Feed nested columns as canonical text to either kernel, expose only opaque statistics.

    Snapshots use the existing text-state encoding; the original schema remains the structure.
    """

    def __init__(self, schema: Any, mode: str) -> None:
        self.schema = schema
        self.nested = {i for i, f in enumerate(schema) if pa.types.is_nested(f.type)}
        self.wire_schema = wire_schema(schema)
        self.state = get_kernel().ProfileState(self.wire_schema, mode)

    def update(self, batch: Any) -> None:
        if [f.type for f in batch.schema] != [f.type for f in self.schema]:
            raise ValueError("batch schema differs from the profile schema")
        if not self.nested:
            self.state.update(batch)
            return
        arrays = [
            pa.array(canonical_values(c), pa.string()) if i in self.nested else c
            for i, c in enumerate(batch.columns)
        ]
        self.state.update(pa.RecordBatch.from_arrays(arrays, schema=self.wire_schema))

    def finalize(self, top_n: int = 500) -> dict[str, Any]:
        result: dict[str, Any] = self.state.finalize(top_n)
        for i in self.nested:
            col = result["columns"][i]
            length = col["length"]
            count = length["count"]
            hist = length.get("hist", {})
            total = sum(int(k) * v for k, v in hist.items())
            # Bounded text states also retain exact length histograms in both kernels.
            size = {
                "format": "shape-nested-size",
                "version": 1,
                "unit": "utf8-bytes",
                "count": count,
                "total": total,
                "min": length.get("min"),
                "max": length.get("max"),
                "mean": length.get("mean"),
            }
            result["columns"][i] = {
                k: col[k] for k in ("name", "count", "null_count", "distinct", "distinct_exact")
            }
            result["columns"][i].update(
                kind="nested", structure=str(self.schema.field(i).type), serialized_size=size
            )
        return result

    def snapshot(self) -> bytes:
        return bytes(self.state.snapshot())

    @property
    def rows(self) -> int:
        return int(self.state.rows)

    @property
    def mode(self) -> str:
        return str(self.state.mode)

    @classmethod
    def from_snapshot(cls, schema: Any, raw: bytes) -> ProfileState:
        out = cls(schema, "bounded")
        out.state = get_kernel().ProfileState.from_snapshot(out.wire_schema, raw)
        return out

    def merge(self, other: ProfileState) -> None:
        if other is self:
            raise ValueError("cannot merge a state into itself")
        if not self.schema.equals(other.schema, check_metadata=False):
            raise ValueError("profile state schemas differ")
        self.state.merge(other.state)
