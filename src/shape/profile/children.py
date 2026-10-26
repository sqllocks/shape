"""Child columns stay below their owner; Arrow offsets and parent nulls define their rows."""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]


def validate_depth(value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 64:
        raise ValueError("max_depth must be an integer from 0 to 64")


def field_path(parent: str, name: str) -> str:
    return parent + ("." + name if name.isidentifier() else "[" + json.dumps(name) + "]")


def arrays(array: Any) -> list[tuple[str, Any, bool]]:
    """Immediate child arrays, excluding null parents (including their physical slots)."""
    a = array.combine_chunks() if isinstance(array, pa.ChunkedArray) else array
    a = pc.filter(a, a.is_valid())
    t = a.type
    if pa.types.is_struct(t):
        return [(f.name, a.field(i), True) for i, f in enumerate(t)]
    if pa.types.is_list(t) or pa.types.is_large_list(t) or pa.types.is_fixed_size_list(t):
        return [("[]", pc.list_flatten(a), False)]
    if pa.types.is_map(t):
        return [("{key}", a.keys, False), ("{value}", a.items, False)]
    return []


def child_path(parent: str, name: str, struct: bool = False) -> str:
    return parent + name if not struct else field_path(parent, name)


def binary_storage(array: Any) -> Any:
    """Binary leaf storage, including binary-backed extensions, without text decoding."""
    a = array.combine_chunks() if isinstance(array, pa.ChunkedArray) else array
    return binary_storage(a.storage) if isinstance(a.type, pa.BaseExtensionType) else a


def is_binary_leaf(typ: Any) -> bool:
    if isinstance(typ, pa.BaseExtensionType):
        return is_binary_leaf(typ.storage_type)
    return bool(pa.types.is_fixed_size_binary(typ))


def lengths(array: Any) -> Any | None:
    t = array.type
    if pa.types.is_map(t):
        a = array.combine_chunks() if isinstance(array, pa.ChunkedArray) else array
        a = pc.filter(a, a.is_valid())
        return pc.subtract(a.offsets.slice(1), a.offsets.slice(0, len(a)))
    if pa.types.is_list(t) or pa.types.is_large_list(t) or pa.types.is_fixed_size_list(t):
        return pc.drop_null(pc.list_value_length(array))
    return None


def reference_children(array: Any, path: str, depth: int) -> list[dict[str, Any]]:
    from .reference.column import _profile_column
    from .reference.model import ColumnProfile
    from .reference.profile import _column_dict
    from .reference.readers import _arrow_cols

    out = []
    for name, arr, struct in arrays(array):
        if is_binary_leaf(arr.type):
            # These leaves were supported inside opaque parents before child profiling.
            # They have no scalar profiler; never decode arbitrary bytes as text.
            doc = _column_dict(
                ColumnProfile(
                    name=name,
                    dtype="other",
                    null_count=arr.null_count,
                    null_rate=arr.null_count / len(arr) if len(arr) else None,
                    cardinality=int(pc.count_distinct(binary_storage(arr)).as_py()),
                    cardinality_ratio=None,
                    is_unique=None,
                    is_enum=False,
                    enum_values=None,
                    min_value=None,
                    max_value=None,
                    mean=None,
                    std=None,
                    distribution=None,
                    distribution_params=None,
                    pattern=None,
                    is_primary_key=False,
                    is_foreign_key=False,
                    fk_ref_table=None,
                )
            )
            doc.update(
                arrow_type=str(arr.type), path=child_path(path, name, struct), row_count=len(arr)
            )
            if struct:
                doc["present"] = (len(arr) - arr.null_count) / len(arr) if len(arr) else None
            out.append(doc)
            continue
        col = _arrow_cols(pa.table({name: arr}))[0]
        col.nested_depth = depth - 1
        col.nested_path = child_path(path, name, struct)
        doc = _column_dict(_profile_column(col, len(arr), keep_uniques=False).prof)
        doc.update(path=col.nested_path, row_count=len(arr))
        if struct:
            doc["present"] = (len(arr) - arr.null_count) / len(arr) if len(arr) else None
        out.append(doc)
    return out


def walk(columns: Mapping[str, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    """Child paths, without exposing them as independent persisted columns."""
    for col in columns.values():
        for child in col.get("children", []):
            yield child["path"], child
            yield from walk({"child": child})
