"""Shared nested-shape validation and explicit JSON text fallback for sinks."""

from __future__ import annotations

import json
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.types as pat  # type: ignore[import-untyped]

from shape.errors import ShapeError


def is_nested(kind: pa.DataType) -> bool:
    """Whether a column is a list, struct or map, including dictionary encoding."""
    if pat.is_dictionary(kind):
        return is_nested(kind.value_type)
    return bool(
        pat.is_list(kind)
        or pat.is_large_list(kind)
        or pat.is_fixed_size_list(kind)
        or pat.is_struct(kind)
        or pat.is_map(kind)
    )


def nested_mode(value: Any, *, typed: bool = False) -> str:
    """Validate a nested option without opening a database connection."""
    allowed = ("native", "json", "typed") if typed else ("native", "json")
    if value not in allowed:
        raise ShapeError("nested must be " + " or ".join(allowed))
    return str(value)


def validate_json_shape(kind: pa.DataType, column: str) -> None:
    """Reject schema-known ambiguity that neither native nor JSON can preserve."""
    if pat.is_dictionary(kind):
        validate_json_shape(kind.value_type, column)
    elif pat.is_struct(kind):
        names = [child.name for child in kind]
        if len(names) != len(set(names)):
            raise ShapeError(
                f"column {column!r}: duplicate struct field names are ambiguous; "
                "rename fields to distinct names"
            )
        for child in kind:
            validate_json_shape(child.type, column)
    elif pat.is_list(kind) or pat.is_large_list(kind) or pat.is_fixed_size_list(kind):
        validate_json_shape(kind.value_type, column)
    elif pat.is_map(kind):
        validate_json_shape(kind.key_type, column)
        validate_json_shape(kind.item_type, column)


def json_batch(batch: pa.RecordBatch) -> pa.RecordBatch:
    """Encode top-level nested columns as JSON strings; preserve SQL NULL containers.

    Maps use ordered key/value pairs, matching Arrow's Python representation and the
    existing SQL text path. Map order and JSON key order are not semantic guarantees.
    """
    arrays = []
    fields = []
    for field, column in zip(batch.schema, batch.columns, strict=True):
        if is_nested(field.type):
            validate_json_shape(field.type, field.name)
            column = pa.array(
                [
                    None
                    if value is None
                    else json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))
                    for value in column.to_pylist()
                ],
                type=pa.string(),
            )
            field = field.with_type(pa.string())
        arrays.append(column)
        fields.append(field)
    return pa.RecordBatch.from_arrays(
        arrays, schema=pa.schema(fields, metadata=batch.schema.metadata)
    )
