"""Nested columns (list, struct, map, union) in a profile.

A nested column is recorded as a whole: its exact Arrow type, the row count, the null count and
rate and, for a list or a map, the distribution of its lengths. The fields inside a nested value
are not profiled (that is future work). The profile names the column's ``dtype`` ``"nested"`` and
adds three keys that only a nested column has:

``arrow_type``
    ``str(type)``, for people (``list<item: int64>``).
``arrow_type_spec``
    The exact type (field names, nullability, units, time zones, nesting) as JSON, for programs:
    :func:`arrow_type_from_spec` returns the type that was profiled. A leaf type is its Arrow name
    (``int64``) or, when the name does not read back as the same type, an Arrow IPC schema in
    base64. Field metadata is not kept.
``nested``
    ``kind`` (``list``, ``large_list``, ``fixed_size_list``, ``list_view``, ``large_list_view``,
    ``map``, ``struct`` or ``union``), ``rows`` and ``non_null`` (values that are not null),
    ``length`` (lists and maps; absent without a value) and ``element_null_rate`` (the share of
    null list elements or map values; absent without an element). ``length`` holds ``min``,
    ``max``, ``mean``, ``std`` (population), ``p50``, ``p95`` and ``hist`` (length to count, up to
    :data:`MAX_HIST` distinct lengths).
"""

from __future__ import annotations

import base64
from typing import Any

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]

#: The most distinct lengths a ``hist`` keeps; a column with more records the summary alone.
MAX_HIST = 256

_LIST_KINDS = ("list", "large_list", "fixed_size_list", "list_view", "large_list_view")


def is_nested_type(typ: pa.DataType) -> bool:
    """True for a list, struct, map or union type (Arrow's ``is_nested``)."""
    return bool(pa.types.is_nested(typ))


def _is(typ: pa.DataType, check: str) -> bool:
    found = getattr(pa.types, check, None)
    return bool(found(typ)) if found else False


def nested_kind(typ: pa.DataType) -> str:
    """The family of a nested type: ``list``, ``large_list``, ``fixed_size_list``, ``list_view``,
    ``large_list_view``, ``map``, ``struct`` or ``union``."""
    if pa.types.is_map(typ):
        return "map"
    if pa.types.is_struct(typ):
        return "struct"
    if pa.types.is_union(typ):
        return "union"
    if pa.types.is_fixed_size_list(typ):
        return "fixed_size_list"
    if _is(typ, "is_large_list_view"):  # list views exist from pyarrow 16
        return "large_list_view"
    if _is(typ, "is_list_view"):
        return "list_view"
    if pa.types.is_large_list(typ):
        return "large_list"
    return "list"


def _leaf_ipc(typ: pa.DataType) -> str:
    schema = pa.schema([pa.field("c", typ)])
    return base64.b64encode(schema.serialize().to_pybytes()).decode("ascii")


def _leaf_from_ipc(text: str) -> pa.DataType:
    schema = pa.ipc.read_schema(pa.py_buffer(base64.b64decode(text, validate=True)))
    if len(schema) != 1:
        raise ValueError("expected one field")
    return schema.field(0).type


def _alias(typ: pa.DataType) -> str | None:
    """``str(typ)`` when Arrow reads that text back as the same type (``int64``, ``string``)."""
    text = str(typ)
    try:
        return text if pa.type_for_alias(text).equals(typ) else None
    except (ValueError, KeyError, TypeError):
        return None


def _field_spec(field: pa.Field) -> dict[str, Any]:
    return {"name": field.name, "nullable": field.nullable, "type": _type_spec(field.type)}


def _field_from(spec: dict[str, Any]) -> pa.Field:
    return pa.field(spec["name"], _type_from(spec["type"]), nullable=spec["nullable"])


def _type_spec(typ: pa.DataType) -> Any:
    if pa.types.is_map(typ):
        return {
            "map": {
                "keys_sorted": typ.keys_sorted,
                "key": _field_spec(typ.key_field),
                "item": _field_spec(typ.item_field),
            }
        }
    if pa.types.is_struct(typ):
        return {"struct": [_field_spec(typ.field(i)) for i in range(typ.num_fields)]}
    if pa.types.is_union(typ):
        return {
            "union": "sparse" if typ.mode == "sparse" else "dense",
            "type_codes": list(typ.type_codes),
            "fields": [_field_spec(typ.field(i)) for i in range(typ.num_fields)],
        }
    if pa.types.is_nested(typ):  # what is left is a list family
        kind = nested_kind(typ)
        spec: dict[str, Any] = {"list": kind, "item": _field_spec(typ.value_field)}
        if kind == "fixed_size_list":
            spec["size"] = typ.list_size
        return spec
    alias = _alias(typ)
    return alias if alias is not None else {"ipc": _leaf_ipc(typ)}


def _list_maker(kind: str) -> Any:
    return getattr(pa, {"list": "list_"}.get(kind, kind))


def _type_from(spec: Any) -> pa.DataType:
    if isinstance(spec, str):
        return pa.type_for_alias(spec)
    if "ipc" in spec:
        return _leaf_from_ipc(spec["ipc"])
    if "map" in spec:
        m = spec["map"]
        return pa.map_(_field_from(m["key"]), _field_from(m["item"]), keys_sorted=m["keys_sorted"])
    if "struct" in spec:
        return pa.struct([_field_from(f) for f in spec["struct"]])
    if "union" in spec:
        make = pa.sparse_union if spec["union"] == "sparse" else pa.dense_union
        return make([_field_from(f) for f in spec["fields"]], list(spec["type_codes"]))
    item = _field_from(spec["item"])
    if spec["list"] == "fixed_size_list":
        return pa.list_(item, int(spec["size"]))
    return _list_maker(spec["list"])(item)


def arrow_type_to_spec(typ: pa.DataType) -> dict[str, Any]:
    """``typ`` exactly (field names, nullability, units, time zones, nesting) as a JSON-ready
    dictionary: ``{"type": ...}``. Field metadata is not kept."""
    return {"type": _type_spec(typ)}


def arrow_type_from_spec(spec: dict[str, Any]) -> pa.DataType:
    """The Arrow type :func:`arrow_type_to_spec` wrote. Raises ``ValueError`` for a document that
    is not one."""
    try:
        return _type_from(spec["type"])
    except (KeyError, TypeError, ValueError, pa.ArrowException) as exc:
        raise ValueError(f"not an Arrow type in the profile: {exc}") from exc


def type_signature(typ: pa.DataType) -> str:
    """``typ`` as the drift check compares nested types: the structure and the leaf types, without
    what Arrow writers name differently for the same type (a list's item field, a map's entry
    and field names) and without nullability. Struct field names stay: they are the schema."""
    if pa.types.is_map(typ):
        return f"map<{type_signature(typ.key_type)}, {type_signature(typ.item_type)}>"
    if pa.types.is_struct(typ):
        fields = ", ".join(
            f"{typ.field(i).name}: {type_signature(typ.field(i).type)}"
            for i in range(typ.num_fields)
        )
        return f"struct<{fields}>"
    if pa.types.is_union(typ):
        fields = ", ".join(type_signature(typ.field(i).type) for i in range(typ.num_fields))
        return f"{'sparse' if typ.mode == 'sparse' else 'dense'}_union<{fields}>"
    if pa.types.is_nested(typ):
        kind = nested_kind(typ)
        size = f"[{typ.list_size}]" if kind == "fixed_size_list" else ""
        return f"{kind}<{type_signature(typ.value_type)}>{size}"
    return str(typ)


def _lengths(col: pa.ChunkedArray) -> np.ndarray[Any, Any]:
    """The lengths of the non-null lists or maps of ``col``."""
    present = col.drop_null() if col.null_count else col
    try:
        lens = pc.list_value_length(present)
    except pa.ArrowNotImplementedError:  # a view type on an Arrow without the kernel
        return np.array([len(v) for v in present.to_pylist()], dtype=np.int64)
    return np.asarray(lens.to_numpy(zero_copy_only=False), dtype=np.int64)


def _element_null_rate(col: pa.ChunkedArray) -> float | None:
    """The share of null elements (list) or null values (map) among all elements, else ``None``."""
    present = col.drop_null() if col.null_count else col
    try:
        flat = pc.list_flatten(present)
    except pa.ArrowNotImplementedError:
        return None
    if pa.types.is_map(col.type):
        flat = flat.field(1)  # the map's values; a key is never null
    total = len(flat)
    return round(flat.null_count / total, 6) if total else None


def length_summary(lens: np.ndarray[Any, Any]) -> dict[str, Any]:
    """The length distribution of ``lens`` (a non-empty array): see the module docstring."""
    out: dict[str, Any] = {
        "min": int(lens.min()),
        "max": int(lens.max()),
        "mean": round(float(lens.mean()), 6),
        "std": round(float(lens.std()), 6),
        "p50": float(np.percentile(lens, 50)),
        "p95": float(np.percentile(lens, 95)),
    }
    values, counts = np.unique(lens, return_counts=True)
    if len(values) <= MAX_HIST:
        out["hist"] = {str(int(v)): int(n) for v, n in zip(values, counts, strict=True)}
    return out


def nested_stats(col: pa.ChunkedArray, rows: int) -> dict[str, Any]:
    """The ``nested`` entry of a column profile (see the module docstring)."""
    kind = nested_kind(col.type)
    non_null = len(col) - col.null_count
    out: dict[str, Any] = {"kind": kind, "rows": rows, "non_null": non_null}
    if kind in _LIST_KINDS or kind == "map":
        if non_null:
            out["length"] = length_summary(_lengths(col))
            rate = _element_null_rate(col)
            if rate is not None:
                out["element_null_rate"] = rate
    return out


def _hist_stats(hists: list[dict[str, int]]) -> dict[str, Any]:
    lens = np.repeat(
        np.array([int(k) for h in hists for k in h], dtype=np.int64),
        np.array([int(n) for h in hists for n in h.values()], dtype=np.int64),
    )
    return length_summary(lens)


def merge_nested(parts: list[dict[str, Any]]) -> dict[str, Any]:
    """The ``nested`` entry of the column that is these entries together (profiles of parts of one
    table). The length distribution is exact when every part kept its ``hist``; otherwise it keeps
    the extremes and the mean, which weights each part by its non-null values."""
    kind = parts[0]["kind"]
    out: dict[str, Any] = {
        "kind": kind,
        "rows": sum(int(p["rows"]) for p in parts),
        "non_null": sum(int(p["non_null"]) for p in parts),
    }
    with_len = [p for p in parts if p.get("length")]
    if with_len:
        hists = [p["length"].get("hist") for p in with_len]
        if all(h is not None for h in hists):
            out["length"] = _hist_stats([h for h in hists if h is not None])
        else:
            weights = [int(p["non_null"]) for p in with_len]
            total = sum(weights)
            mean = sum(p["length"]["mean"] * w for p, w in zip(with_len, weights, strict=True))
            out["length"] = {
                "min": min(p["length"]["min"] for p in with_len),
                "max": max(p["length"]["max"] for p in with_len),
                "mean": round(mean / total, 6) if total else 0.0,
            }
    rated = [p for p in parts if p.get("element_null_rate") is not None and p.get("length")]
    if rated:  # weight by the elements each part holds (mean length times non-null values)
        sizes = [p["length"]["mean"] * int(p["non_null"]) for p in rated]
        total = sum(sizes)
        if total:
            share = sum(p["element_null_rate"] * s for p, s in zip(rated, sizes, strict=True))
            out["element_null_rate"] = round(share / total, 6)
    return out
