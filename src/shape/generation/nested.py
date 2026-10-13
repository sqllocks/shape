"""Generate a nested column (list, struct, map) from its profile entry.

``generate`` from a profile writes a nested column with the Arrow type the profile recorded
(``shape.profile.nested``), the recorded null rate (the engine's null mask) and, for a list or a
map, lengths drawn from the recorded length distribution. The values inside are not profiled, so
each leaf comes from the existing per-type generator of its type, as a column of that type with no
statistics would (``SchemaBuilder.column_generator`` on a blank profile): that is what
``generate`` already does for a profile column it knows nothing about. What a nested value holds
beyond its type is therefore not modelled.

Everything is row addressed, like every strategy: the value of row ``r`` depends on the seed, the
column and ``r`` alone, whatever the chunking. A list of up to ``M`` elements takes ``M`` slots;
slot ``j`` is generated for all the rows of the chunk by the child's generator (its own stream) and
the rows whose list is longer than ``j`` keep it, so a chunk costs ``rows x M`` child values.

Choices (``docs/PROFILING_NOTES.md``): a nested value with no recorded lengths (a list inside a
list, inside a struct, or a map inside a list) has 1 to 3 elements; a struct field is never null;
the entries of a map have distinct keys (strings and integers get the slot number built in, any
other key type drops the repeated keys, so such a map can be shorter); a child type with no
generator (union, extension, run-end encoded and the view types) is refused by name.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from typing import Any

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]

from shape.generation.arrowkit import array as arrow_array
from shape.generation.rng import RowStream
from shape.plugins.api.v1 import GenerationContext
from shape.profile.nested import arrow_type_from_spec, nested_kind

NESTED = "nested"
#: Lengths of a nested value inside a nested value, which has no recorded lengths.
DEFAULT_MIN_LENGTH, DEFAULT_MAX_LENGTH = 1, 3

_LIST_KINDS = ("list", "large_list", "fixed_size_list", "list_view", "large_list_view")


def unsupported_child(typ: pa.DataType) -> pa.DataType | None:
    """The first type inside ``typ`` (``typ`` itself when it is a leaf) that no generator
    produces, else ``None``."""
    if pa.types.is_dictionary(typ):
        return unsupported_child(typ.value_type)
    if pa.types.is_struct(typ):
        for i in range(typ.num_fields):
            bad = unsupported_child(typ.field(i).type)
            if bad is not None:
                return bad
        return None
    if pa.types.is_map(typ):
        return unsupported_child(typ.key_type) or unsupported_child(typ.item_type)
    if pa.types.is_union(typ):
        return typ
    if pa.types.is_nested(typ):
        if nested_kind(typ) in ("list_view", "large_list_view"):
            return typ
        return unsupported_child(typ.value_type)
    ok = (
        pa.types.is_null,
        pa.types.is_boolean,
        pa.types.is_integer,
        pa.types.is_floating,
        pa.types.is_decimal,
        pa.types.is_string,
        pa.types.is_large_string,
        pa.types.is_date,
        pa.types.is_timestamp,
        pa.types.is_time,
        pa.types.is_binary,
        pa.types.is_large_binary,
        pa.types.is_fixed_size_binary,
        pa.types.is_duration,
    )
    return None if any(check(typ) for check in ok) else typ


def check_generatable(spec: Mapping[str, Any], where: str) -> None:
    """Raise ``ValueError`` naming ``where`` and the type when a child type has no generator."""
    typ = arrow_type_from_spec(spec["arrow_type_spec"])
    bad = unsupported_child(typ)
    if bad is not None:
        raise ValueError(
            f"{where}: cannot generate the nested column {typ}: no generator for the child "
            f"type {bad}"
        )


def _blank_generator(name: str, dtype: str) -> dict[str, Any]:
    """The generator ``SchemaBuilder`` gives a column of ``dtype`` named ``name`` with no
    statistics."""
    from shape.generation.learn import SchemaBuilder
    from shape.profile.reference.model import ColumnProfile

    blank = ColumnProfile(
        name=name,
        dtype=dtype,
        null_count=0,
        null_rate=0.0,
        cardinality=0,
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
    return SchemaBuilder().column_generator(blank, {})


def _slot(ctx: GenerationContext, path: str) -> GenerationContext:
    """``ctx`` for the child at ``path``: the same rows, a stream of its own."""
    return dataclasses.replace(ctx, column=f"{ctx.column}{path}")


def _lengths(
    length: Mapping[str, Any] | None, fixed: int | None, ctx: GenerationContext, path: str
) -> np.ndarray[Any, Any]:
    """One length per row of ``ctx``: the recorded distribution, else the default."""
    n = ctx.n_rows
    if fixed is not None:
        return np.full(n, fixed, dtype=np.int64)
    u = RowStream(ctx.seed, ctx.table, ctx.column, f"nested.length{path}").uniform(ctx.row_start, n)
    if not length:
        span = DEFAULT_MAX_LENGTH - DEFAULT_MIN_LENGTH + 1
        return DEFAULT_MIN_LENGTH + np.minimum((u * span).astype(np.int64), span - 1)
    hist = length.get("hist")
    if hist:
        sizes = np.array(sorted(int(k) for k in hist), dtype=np.int64)
        weights = np.array([hist[str(k)] for k in sizes], dtype=np.float64)
        cdf = np.cumsum(weights) / weights.sum()
        return sizes[np.minimum(np.searchsorted(cdf, u, side="right"), len(sizes) - 1)]
    z = RowStream(ctx.seed, ctx.table, ctx.column, f"nested.length{path}.z").normal(
        ctx.row_start, n
    )
    drawn = np.rint(float(length["mean"]) + float(length.get("std") or 0.0) * z)
    return np.clip(drawn, int(length["min"]), int(length["max"])).astype(np.int64)


def _leaf(typ: pa.DataType, ctx: GenerationContext, path: str, name: str) -> pa.Array:
    """``ctx.n_rows`` values of the leaf type ``typ``."""
    n = ctx.n_rows
    if pa.types.is_null(typ):
        return pa.nulls(n)
    if pa.types.is_dictionary(typ):
        values = _leaf(typ.value_type, ctx, path, name)
        return values.dictionary_encode().cast(typ)
    if pa.types.is_duration(typ):
        return _duration(typ, ctx, path)
    if (
        pa.types.is_binary(typ)
        or pa.types.is_large_binary(typ)
        or pa.types.is_fixed_size_binary(typ)
    ):
        return _binary(typ, ctx, path)
    from shape.profile.reference.readers import declared_name

    dtype = declared_name(typ)
    gen = _blank_generator(name, dtype)
    engine = getattr(ctx, "engine", None)
    impl = engine._strategy(str(gen["strategy"])) if engine is not None else None
    if impl is None:
        raise ValueError(f"{ctx.table}.{ctx.column}: no strategy {gen['strategy']!r}")
    produced = impl.generate(gen, _slot(ctx, path))
    arr = produced.combine_chunks() if isinstance(produced, pa.ChunkedArray) else produced
    if not isinstance(arr, pa.Array):
        arr = arrow_array(arr)
    return _to_leaf(arr, typ)


#: Bytes of a binary child with no recorded length, and the span of a duration child (one day).
DEFAULT_MIN_BYTES, DEFAULT_MAX_BYTES = 1, 16
_UNIT_PER_SECOND = {"s": 1, "ms": 1_000, "us": 1_000_000, "ns": 1_000_000_000}


def _duration(typ: pa.DataType, ctx: GenerationContext, path: str) -> pa.Array:
    """Durations drawn uniformly from 0 to one day, in the unit of ``typ``."""
    u = RowStream(ctx.seed, ctx.table, ctx.column, f"nested.duration{path}").uniform(
        ctx.row_start, ctx.n_rows
    )
    span = 86_400 * _UNIT_PER_SECOND[typ.unit]
    return pa.array(np.minimum((u * span).astype(np.int64), span - 1), pa.int64()).cast(typ)


def _binary(typ: pa.DataType, ctx: GenerationContext, path: str) -> pa.Array:
    """Random bytes (seeded, row addressed): ``byte_width`` of them for a fixed-size type, else
    ``DEFAULT_MIN_BYTES`` to ``DEFAULT_MAX_BYTES``."""
    n = ctx.n_rows
    fixed = typ.byte_width if pa.types.is_fixed_size_binary(typ) else None
    top = fixed if fixed is not None else DEFAULT_MAX_BYTES
    words = RowStream(ctx.seed, ctx.table, ctx.column, f"nested.bytes{path}").raw(
        ctx.row_start, n, per_row=max((top + 7) // 8, 1)
    )
    grid = np.ascontiguousarray(words).view(np.uint8).reshape(n, -1)[:, :top]
    if fixed is not None:
        return pa.FixedSizeBinaryArray.from_buffers(typ, n, [None, pa.py_buffer(grid.tobytes())])
    span = DEFAULT_MAX_BYTES - DEFAULT_MIN_BYTES + 1
    u = RowStream(ctx.seed, ctx.table, ctx.column, f"nested.bytes{path}.length").uniform(
        ctx.row_start, n
    )
    lens = DEFAULT_MIN_BYTES + np.minimum((u * span).astype(np.int64), span - 1)
    keep = np.arange(top)[None, :] < lens[:, None]
    data = grid[keep]
    wide = pa.types.is_large_binary(typ)
    offsets = np.concatenate([[0], np.cumsum(lens)]).astype(np.int64 if wide else np.int32)
    return pa.Array.from_buffers(
        typ, n, [None, pa.py_buffer(offsets.tobytes()), pa.py_buffer(data.tobytes())]
    )


def _to_leaf(arr: pa.Array, typ: pa.DataType) -> pa.Array:
    if pa.types.is_boolean(typ):
        return arr.cast(pa.bool_()) if not pa.types.is_floating(arr.type) else pc.greater(arr, 0.5)
    if pa.types.is_integer(typ):
        if pa.types.is_floating(arr.type):
            arr = pc.round(arr)
        return arr.cast(typ, safe=False)
    if pa.types.is_floating(typ) or pa.types.is_string(typ) or pa.types.is_large_string(typ):
        return arr.cast(typ)
    if pa.types.is_decimal(typ):
        scale = typ.scale
        top = 10.0 ** (typ.precision - scale) - 10.0**-scale  # the largest value that fits
        rounded = pc.round(arr.cast(pa.float64()), scale)
        return pc.min_element_wise(pc.abs(rounded), top).cast(typ)
    if pa.types.is_date(typ):
        return arr.cast(typ)
    if pa.types.is_time(typ):
        return arr.cast(pa.timestamp("us")).cast(pa.time64("us")).cast(typ)
    return arr.cast(typ)  # a timestamp, in its unit and zone


def _unique_keys(
    keys: list[pa.Array], lengths: np.ndarray[Any, Any], key_type: pa.DataType
) -> tuple[list[pa.Array], np.ndarray[Any, Any]]:
    """Slot ``j`` of a map's keys made distinct within a row; returns the keys and the lengths.

    A string key gets ``_j`` appended and an integer key ``j`` added to the row's first key, so
    keys differ by construction. Any other key type keeps the first occurrence and drops repeats
    (the row's map is then shorter)."""
    if pa.types.is_string(key_type) or pa.types.is_large_string(key_type):
        return [
            pc.binary_join_element_wise(k, pa.scalar(f"_{j}", key_type), "")
            for j, k in enumerate(keys)
        ], lengths
    if pa.types.is_integer(key_type) and keys:
        info = np.iinfo(key_type.to_pandas_dtype())
        base = np.asarray(keys[0].to_numpy(zero_copy_only=False), dtype=np.int64)
        span = int(info.max) - int(info.min) - len(keys)
        shifted = [
            ((base - int(info.min)) % max(span, 1) + int(info.min) + j) for j in range(len(keys))
        ]
        return [pa.array(s, pa.int64()).cast(key_type, safe=False) for s in shifted], lengths
    return keys, lengths


def _list_like(
    typ: pa.DataType,
    ctx: GenerationContext,
    path: str,
    length: Mapping[str, Any] | None,
    element_null_rate: float | None,
) -> pa.Array:
    n = ctx.n_rows
    kind = nested_kind(typ)
    is_map = kind == "map"
    fixed = typ.list_size if kind == "fixed_size_list" else None
    lens = _lengths(length, fixed, ctx, path)
    top = int(lens.max()) if n else 0
    value_type = typ.item_type if is_map else typ.value_type
    key_type = typ.key_type if is_map else None
    rows: list[np.ndarray[Any, Any]] = []
    items: list[pa.Array] = []
    keys: list[pa.Array] = []
    for j in range(top):
        live = np.nonzero(lens > j)[0]
        take = pa.array(live, pa.int64())
        item = _values(value_type, ctx, f"{path}[{j}].v")
        if element_null_rate:
            u = RowStream(ctx.seed, ctx.table, ctx.column, f"nested.element{path}[{j}]").uniform(
                ctx.row_start, n
            )
            item = pc.if_else(pa.array(u < element_null_rate), pa.scalar(None, item.type), item)
        items.append(item.take(take))
        if is_map:
            keys.append(_values(key_type, ctx, f"{path}[{j}].k"))
        rows.append(live)
    if is_map and keys:
        keys, lens = _unique_keys(keys, lens, key_type)
        keys = [k.take(pa.array(r, pa.int64())) for k, r in zip(keys, rows, strict=True)]
    if rows:
        row_ids = np.concatenate(rows)
        order = np.argsort(
            row_ids * max(top, 1)
            + np.concatenate([np.full(len(r), j) for j, r in enumerate(rows)]),
            kind="stable",
        )
        child = pa.concat_arrays(items).take(pa.array(order, pa.int64()))
        child_keys = pa.concat_arrays(keys).take(pa.array(order, pa.int64())) if is_map else None
    else:
        child = pa.array([], value_type)
        child_keys = pa.array([], key_type) if is_map else None
    if is_map:
        child_keys, child, lens = _distinct_entries(child_keys, child, lens, key_type)
    offsets = np.concatenate([[0], np.cumsum(lens)])
    if kind == "fixed_size_list":
        return pa.FixedSizeListArray.from_arrays(child, type=typ)
    if kind == "large_list":
        return pa.LargeListArray.from_arrays(pa.array(offsets, pa.int64()), child, type=typ)
    offs = pa.array(offsets, pa.int32())
    if is_map:
        return pa.MapArray.from_arrays(offs, child_keys, child, type=typ)
    return pa.ListArray.from_arrays(offs, child, type=typ)


def _distinct_entries(
    keys: pa.Array, items: pa.Array, lens: np.ndarray[Any, Any], key_type: pa.DataType
) -> tuple[pa.Array, pa.Array, np.ndarray[Any, Any]]:
    """Drop the second and later entries of a map that repeat a key (only keys of a type the
    slot number was not built into can); the lengths follow."""
    if len(keys) == 0 or pa.types.is_string(key_type) or pa.types.is_large_string(key_type):
        return keys, items, lens
    if pa.types.is_integer(key_type):
        return keys, items, lens
    row = np.repeat(np.arange(len(lens)), lens)
    seen: set[tuple[int, Any]] = set()
    keep = np.ones(len(keys), dtype=bool)
    for i, k in enumerate(keys.to_pylist()):
        if (int(row[i]), k) in seen:
            keep[i] = False
        else:
            seen.add((int(row[i]), k))
    if keep.all():
        return keys, items, lens
    mask = pa.array(keep)
    kept = np.bincount(row[keep], minlength=len(lens)).astype(np.int64)
    return keys.filter(mask), items.filter(mask), kept


def _values(typ: pa.DataType, ctx: GenerationContext, path: str) -> pa.Array:
    """``ctx.n_rows`` values of ``typ`` with no recorded statistics (a child of a nested value)."""
    if pa.types.is_struct(typ):
        arrays = [
            _values(typ.field(i).type, ctx, f"{path}.{typ.field(i).name}")
            for i in range(typ.num_fields)
        ]
        return pa.StructArray.from_arrays(
            arrays, fields=[typ.field(i) for i in range(typ.num_fields)]
        )
    if pa.types.is_nested(typ):
        return _list_like(typ, ctx, path, None, None)
    name = path.rsplit(".", 1)[-1] if "." in path else "item"
    return _leaf(typ, ctx, path, name)


class NestedStrategy:
    """The ``nested`` strategy: a nested column of the recorded Arrow type.

    ``spec['arrow_type_spec']`` is the profile's exact type, ``spec['length']`` the length
    distribution of a list or a map (``shape.profile.nested``) and ``spec['element_null_rate']``
    the share of null elements. The null rate of the column is the engine's (``null_rate``)."""

    name = NESTED
    generator_version = 1

    def generate(self, spec: Mapping[str, Any], ctx: GenerationContext) -> pa.Array:
        where = f"{ctx.table}.{ctx.column}"
        check_generatable(spec, where)
        typ = arrow_type_from_spec(spec["arrow_type_spec"])
        if pa.types.is_struct(typ) or not pa.types.is_nested(typ):
            return _values(typ, ctx, "")
        if pa.types.is_union(typ):  # refused above
            raise ValueError(f"{where}: cannot generate a union column")
        return _list_like(typ, ctx, "", spec.get("length"), spec.get("element_null_rate"))
