"""Reading a column of another table of the same run, by key: what ``lookup`` (and the inline
lookup of ``conditional``) build on.

:class:`KeyIndex` finds the first row of a key column holding each probe value. It is built once
per ``(engine, table, column)`` and cached for the life of the engine, so a chunk costs one binary
search per row, not a scan of the parent table. Numeric keys compare by value (an integer key
matches the same number held as a float); everything else compares as it is.

:func:`lookup_values` is the whole operation: the ``source_column`` of ``source_table`` for the key
values of ``via`` in the chunk being built. A key with no parent row gives null.

Stable interface: ``KeyIndex``, ``key_index`` and ``lookup_values``.
"""

from __future__ import annotations

import threading
import weakref
from typing import Any

import numpy as np
import numpy.typing as npt
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]

from shape.generation.engine import Engine, EngineContext
from shape.generation.strategy_kit import StrategyError, where
from shape.plugins.api.v1 import GenerationContext


def _is_number(t: pa.DataType) -> bool:
    return bool(pa.types.is_integer(t) or pa.types.is_floating(t))


def _numpy_float(arr: pa.Array) -> npt.NDArray[np.float64]:
    return np.asarray(
        pc.fill_null(arr.cast(pa.float64()), float("nan")).to_numpy(zero_copy_only=False),
        dtype=np.float64,
    )


class KeyIndex:
    """First-row positions of the values of one key column."""

    def __init__(self, keys: pa.Array) -> None:
        self._keys = keys
        self._numeric = _is_number(keys.type)
        self._integral = bool(pa.types.is_integer(keys.type))
        self._sorted: npt.NDArray[Any] | None = None
        self._order: npt.NDArray[np.int64] | None = None
        self._float_sorted: npt.NDArray[np.float64] | None = None
        self._float_order: npt.NDArray[np.int64] | None = None
        self._lock = threading.Lock()

    def _sort(self, as_float: bool) -> tuple[npt.NDArray[Any], npt.NDArray[np.int64]]:
        with self._lock:
            if as_float:
                if self._float_sorted is None or self._float_order is None:
                    flat = _numpy_float(self._keys)
                    order = np.argsort(flat, kind="stable").astype(np.int64)
                    self._float_sorted, self._float_order = flat[order], order
                return self._float_sorted, self._float_order
            if self._sorted is None or self._order is None:
                raw = np.asarray(self._keys.to_numpy(zero_copy_only=False))
                order = np.argsort(raw, kind="stable").astype(np.int64)
                self._sorted, self._order = raw[order], order
            return self._sorted, self._order

    def positions(self, values: pa.Array) -> npt.NDArray[np.int64]:
        """For each value, the row of the first key equal to it, or ``-1`` (a null value, or one
        no key equals)."""
        if len(values) == 0:
            return np.empty(0, dtype=np.int64)
        if self._numeric and _is_number(values.type):
            as_float = not (self._integral and pa.types.is_integer(values.type))
            sorted_keys, order = self._sort(as_float)
            probe = (
                _numpy_float(values)
                if as_float
                else np.asarray(
                    pc.fill_null(values, 0).to_numpy(zero_copy_only=False), dtype=np.int64
                )
            )
            where_ = np.searchsorted(sorted_keys, probe, side="left")
            clipped = np.minimum(where_, max(len(sorted_keys) - 1, 0))
            hit = (where_ < len(sorted_keys)) & (sorted_keys[clipped] == probe)
            hit &= np.asarray(pc.is_valid(values).to_numpy(zero_copy_only=False), dtype=bool)
            return np.where(hit, order[clipped] if len(order) else 0, -1).astype(np.int64)
        probe_arr = values if values.type == self._keys.type else values.cast(pa.string())
        keys = self._keys if values.type == self._keys.type else self._keys.cast(pa.string())
        found = pc.index_in(probe_arr, value_set=keys)
        return np.asarray(pc.fill_null(found, -1).to_numpy(zero_copy_only=False), dtype=np.int64)


_indexes: weakref.WeakKeyDictionary[Engine, dict[tuple[str, str], KeyIndex]] = (
    weakref.WeakKeyDictionary()
)
_index_lock = threading.Lock()
_active = threading.local()


def key_index(engine: Engine, table: str, column: str) -> KeyIndex:
    """The cached :class:`KeyIndex` of ``table.column`` for ``engine`` (the table is generated
    once, in full, the first time)."""
    with _index_lock:
        per_engine = _indexes.setdefault(engine, {})
        found = per_engine.get((table, column))
    if found is not None:
        return found
    built = KeyIndex(engine.generate_table(table)[column].combine_chunks())
    with _index_lock:
        return _indexes.setdefault(engine, {}).setdefault((table, column), built)


def _key_column(
    engine: Engine, ctx: GenerationContext, source: str, via: str, key: str | None
) -> str:
    tdef = engine.schema.tables[source]
    if key is not None:
        if key not in tdef.columns:
            raise StrategyError(f"lookup key {key!r} is not a column of {source} ({where(ctx)})")
        return key
    if via in tdef.columns:
        return via
    own = engine.schema.tables[ctx.table].columns.get(via)
    if own is not None and own.fk_ref_table == source and own.fk_ref_column in tdef.columns:
        return str(own.fk_ref_column)
    if len(tdef.primary_key) == 1:
        return tdef.primary_key[0]
    raise StrategyError(
        f"lookup cannot tell which column of {source} holds the keys {via!r} refers to "
        f"({where(ctx)}); name it with 'key'"
    )


def lookup_values(
    ctx: GenerationContext,
    source_table: str,
    source_column: str,
    via: str,
    key: str | None = None,
) -> pa.Array:
    """``source_table.source_column`` for each row's ``via`` value. ``via`` must be a column of
    the chunk already built. The keys are the column of ``source_table`` named ``key``, else the
    one called ``via``, else the column this table's foreign key ``via`` points at, else the
    source's single-column primary key."""
    engine = getattr(ctx, "engine", None)
    if not isinstance(ctx, EngineContext) or engine is None:
        raise StrategyError(f"lookup needs the generation engine ({where(ctx)})")
    if via not in ctx.columns:
        raise StrategyError(
            f"lookup via column {via!r} is not generated before {where(ctx)}: "
            "define it earlier in the table"
        )
    if source_table not in engine.schema.tables:
        raise StrategyError(
            f"lookup source table {source_table!r} is not in the schema ({where(ctx)})"
        )
    if source_column not in engine.schema.tables[source_table].columns:
        raise StrategyError(
            f"lookup source column {source_table}.{source_column} does not exist ({where(ctx)})"
        )
    active: set[tuple[int, str]] = getattr(_active, "tables", set())
    mark = (id(engine), source_table)
    if source_table == ctx.table or mark in active:
        raise StrategyError(f"lookup of {source_table} is circular ({where(ctx)})")
    key_name = _key_column(engine, ctx, source_table, via, key)
    _active.tables = active | {mark}
    try:
        index = key_index(engine, source_table, key_name)
        source = engine.generate_table(source_table)[source_column].combine_chunks()
    finally:
        _active.tables = active
    pos = index.positions(ctx.columns[via])
    take = pa.array(np.where(pos < 0, 0, pos), mask=pos < 0, type=pa.int64())
    if len(source) == 0:
        return pa.nulls(len(pos), source.type)
    return pc.take(source, take)
