"""Typed wrappers over the generation kernel (``docs/GENERATION_KERNEL.md``).

A strategy calls the kernel through these functions: they pick the implementation
(:mod:`shape.kernel.dispatch`), pass the stream's key and return numpy or ``pyarrow`` objects,
whichever kernel ran. Every result is a function of ``(stream key, row)`` alone, so it does not
depend on how the rows are chunked.

Stable interface: ``AliasTable``, ``alias_table``, ``alias_draw``, ``pool_take``, ``uuid4``,
``random_strings``, ``template_strings``, ``join_strings``, ``string_case``, ``day_weights``,
``hour_weights_peaks`` and ``temporal_sample``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import numpy as np
import numpy.typing as npt
import pyarrow as pa  # type: ignore[import-untyped]

from shape.kernel.dispatch import get_kernel

from .rng import RowStream


def _arrow(value: Any) -> pa.Array:
    return pa.array(value)


def to_numpy(value: Any) -> npt.NDArray[Any]:
    """An Arrow array (or anything ``pyarrow.array`` accepts) as a numpy array."""
    out: npt.NDArray[Any] = np.asarray(_arrow(value).to_numpy(zero_copy_only=False))
    return out


@dataclass(frozen=True, slots=True)
class AliasTable:
    """Vose's alias table: ``size`` categories, ``prob`` and ``alias`` as Arrow arrays."""

    size: int
    prob: pa.Array
    alias: pa.Array


@lru_cache(maxsize=256)
def _alias_cached(weights: tuple[float, ...]) -> AliasTable:
    prob, alias = get_kernel().alias_build(pa.array(weights, type=pa.float64()))
    return AliasTable(len(weights), _arrow(prob), _arrow(alias))


def alias_table(weights: Sequence[float]) -> AliasTable:
    """The alias table of ``weights`` (finite, non-negative, not all zero). Cached by the
    weights, so a strategy may call it for every chunk."""
    return _alias_cached(tuple(float(w) for w in weights))


def alias_draw(
    table: AliasTable, stream: RowStream, row_start: int, n_rows: int, *, slot: int = 0,
    per_row: int = 2,
) -> npt.NDArray[np.int64]:  # fmt: skip
    """The category (``0 .. size - 1``) of rows ``row_start ..``, from words ``slot`` and
    ``slot + 1`` of each row's ``per_row`` words."""
    out = get_kernel().alias_sample(
        table.prob, table.alias, stream.k0, stream.k1, row_start, n_rows, per_row, slot
    )
    return to_numpy(out).astype(np.int64, copy=False)


def pool_take(pool: pa.Array, indices: npt.NDArray[np.integer[Any]]) -> pa.Array:
    """``pool[indices]`` (a ``string`` array)."""
    idx = pa.array(np.asarray(indices, dtype=np.int64), type=pa.int64())
    return _arrow(get_kernel().pool_take(pool, idx))


def uuid4(stream: RowStream, row_start: int, n_rows: int) -> pa.Array:
    """Version-4 UUID strings, two words per row."""
    return _arrow(get_kernel().uuid4_strings(stream.k0, stream.k1, row_start, n_rows))


def random_strings(
    stream: RowStream, row_start: int, n_rows: int, length: int, alphabet: str
) -> pa.Array:
    """``length`` characters from ``alphabet`` per row, ``length`` words per row."""
    return _arrow(
        get_kernel().random_strings(stream.k0, stream.k1, row_start, n_rows, length, alphabet)
    )


def template_strings(
    literals: Sequence[str],
    slots: Sequence[tuple[int, int]],
    columns: Sequence[pa.Array],
    n_rows: int,
) -> pa.Array:
    """``literals[0] + column + literals[1] + ...``: see the kernel documentation."""
    return _arrow(get_kernel().template_strings(list(literals), list(slots), list(columns), n_rows))


def join_strings(columns: Sequence[pa.Array], sep: str, skip_nulls: bool = False) -> pa.Array:
    """The columns' values joined with ``sep``."""
    return _arrow(get_kernel().join_strings(list(columns), sep, skip_nulls))


def string_case(array: pa.Array, mode: str) -> pa.Array:
    """``upper``, ``lower`` or ``title`` case."""
    return _arrow(get_kernel().string_case(array, mode))


def day_weights(
    start_day: int,
    n_days: int,
    month_weights: Sequence[float],
    dow_weights: Sequence[float],
    per_bucket: bool = True,
) -> pa.Array:
    """Per-day weights of a date range from month and weekday profiles."""
    return _arrow(
        get_kernel().day_weights(
            start_day, n_days, list(month_weights), list(dow_weights), per_bucket
        )
    )


def hour_weights_peaks(peaks: Sequence[float], std: float) -> pa.Array:
    """The 24 hour weights of equally likely Gaussian peaks."""
    return _arrow(get_kernel().hour_weights_peaks(list(peaks), std))


def temporal_sample(
    day_weights_: pa.Array,
    hour_weights: pa.Array,
    start_day: int,
    stream: RowStream,
    row_start: int,
    n_rows: int,
    whole_seconds: bool = False,
) -> pa.Array:
    """``timestamp[us]`` values from per-day and per-hour weights, five words per row."""
    return _arrow(
        get_kernel().temporal_sample(
            day_weights_, hour_weights, start_day, stream.k0, stream.k1, row_start, n_rows,
            whole_seconds,
        )
    )  # fmt: skip
