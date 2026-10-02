"""Typed wrappers over the generation kernel (``docs/GENERATION_KERNEL.md``).

A strategy calls the kernel through these functions: they pick the implementation
(:mod:`shape.kernel.dispatch`), pass the stream's key and return numpy or ``pyarrow`` objects,
whichever kernel ran. Every result is a function of ``(stream key, row)`` alone, so it does not
depend on how the rows are chunked.

Stable interface: ``AliasTable``, ``alias_table``, ``alias_draw``, ``alias_pick_pool``,
``alias_pick_values``, ``pool_pick``, ``ZipfTable``, ``zipf_table``, ``zipf_draw``,
``uniform_index``, ``uniform_keys``, ``zipf_keys``, ``range_values``, ``lognormal``,
``compose_strings`` (``PoolPiece``, ``IntPiece``, ``ColumnPiece``), ``pool_take``, ``uuid4``,
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

from shape.generation.arrowkit import array as arrow_array
from shape.generation.arrowkit import to_numpy as arrow_numpy
from shape.kernel.dispatch import get_kernel

from .rng import RowStream


def _arrow(value: Any) -> pa.Array:
    return arrow_array(value)


def to_numpy(value: Any) -> npt.NDArray[Any]:
    """An Arrow array (or anything ``pyarrow.array`` accepts) as a numpy array."""
    out: npt.NDArray[Any] = np.asarray(arrow_numpy(_arrow(value)))
    return out


@dataclass(frozen=True, slots=True)
class AliasTable:
    """Vose's alias table: ``size`` categories, ``prob`` and ``alias`` as Arrow arrays."""

    size: int
    prob: pa.Array
    alias: pa.Array


@lru_cache(maxsize=256)
def _alias_cached(weights: tuple[float, ...]) -> AliasTable:
    prob, alias = get_kernel().alias_build(arrow_array(weights, type=pa.float64()))
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


def alias_pick_pool(
    table: AliasTable, pool: pa.Array, stream: RowStream, row_start: int, n_rows: int
) -> pa.Array:
    """``pool[alias_draw(...)]`` for a ``string`` pool of ``table.size`` entries, in one call."""
    return _arrow(
        get_kernel().alias_pool(
            table.prob, table.alias, pool, stream.k0, stream.k1, row_start, n_rows, 2, 0
        )
    )


def alias_pick_values(
    table: AliasTable, values: pa.Array, stream: RowStream, row_start: int, n_rows: int
) -> pa.Array:
    """``values[alias_draw(...)]`` for a ``float64`` array of ``table.size`` entries (one call)."""
    return _arrow(
        get_kernel().alias_values(
            table.prob, table.alias, values, stream.k0, stream.k1, row_start, n_rows, 2, 0
        )
    )


@dataclass(frozen=True, slots=True)
class ZipfTable:
    """A normalised cumulative weight array and the kernel's guide table for it."""

    size: int
    cum: pa.Array
    guide: pa.Array


def zipf_table(cum: npt.NDArray[np.float64]) -> ZipfTable:
    """The table for ``cum`` (non-decreasing, finite, ending at 1). Keep it: building the guide
    costs about as much as drawing a few chunks."""
    arrow = arrow_array(np.ascontiguousarray(cum, dtype=np.float64), type=pa.float64())
    return ZipfTable(len(cum), arrow, _arrow(get_kernel().zipf_guide(arrow)))


def zipf_draw(
    table: ZipfTable, stream: RowStream, row_start: int, n_rows: int
) -> npt.NDArray[np.int64]:
    """Parent rows ``0 .. size - 1`` of rows ``row_start ..``: ``searchsorted(cum, u, "right")``
    clipped to the last row, for the uniform ``u`` of word 0 of each row."""
    out = get_kernel().zipf_draw(table.cum, table.guide, stream.k0, stream.k1, row_start, n_rows)
    return to_numpy(out).astype(np.int64, copy=False)


def zipf_keys(
    table: ZipfTable, stream: RowStream, row_start: int, n_rows: int, start: int, step: int
) -> pa.Array:
    """:func:`zipf_draw` as the keys ``start + row * step`` of a sequence primary key, as an
    ``int64`` Arrow array, in one call."""
    return _arrow(
        get_kernel().zipf_draw(
            table.cum, table.guide, stream.k0, stream.k1, row_start, n_rows, start, step
        )
    )


def uniform_keys(
    stream: RowStream, row_start: int, n_rows: int, size: int, start: int, step: int
) -> pa.Array:
    """:func:`uniform_index` as the keys ``start + row * step`` of a sequence primary key, as an
    ``int64`` Arrow array, in one call."""
    return _arrow(
        get_kernel().uniform_index(stream.k0, stream.k1, row_start, n_rows, size, 1, 0, start, step)
    )


def range_values(start: int, step: int, row_start: int, n_rows: int) -> pa.Array:
    """``start + (row_start + i) * step`` for ``i`` in ``0 .. n_rows - 1`` (a sequence column), as
    an ``int64`` Arrow array. ``start`` and ``step`` must be int64 values."""
    return _arrow(get_kernel().range_values(start, step, row_start, n_rows))


def uniform_index(
    stream: RowStream, row_start: int, n_rows: int, size: int, *, slot: int = 0, per_row: int = 1
) -> npt.NDArray[np.int64]:
    """``min(floor(u * size), size - 1)`` for the uniform ``u`` of word ``slot`` of each row: the
    index of a uniform draw among ``size`` entries."""
    out = get_kernel().uniform_index(stream.k0, stream.k1, row_start, n_rows, size, per_row, slot)
    return to_numpy(out).astype(np.int64, copy=False)


def pool_pick(pool: pa.Array, stream: RowStream, row_start: int, n_rows: int) -> pa.Array:
    """An entry of the ``string`` array ``pool`` picked uniformly per row (as
    :func:`uniform_index`), in one call."""
    return _arrow(get_kernel().pool_pick(pool, stream.k0, stream.k1, row_start, n_rows))


def lognormal(
    stream: RowStream,
    row_start: int,
    n_rows: int,
    mu: float,
    sigma: float,
    low: float | None = None,
    high: float | None = None,
    scale: int | None = None,
) -> npt.NDArray[np.float64] | None:
    """``exp(mu + sigma * z)`` for the standard normal ``z`` of each row, clipped to ``[low, high]``
    where given and rounded to ``scale`` decimals where given, in one pass: the values of the
    NumPy expression, bit for bit. ``None`` when the kernel cannot do it (numpy's ``exp`` loop is
    not available to it, or ``scale`` is negative or above 22): the caller then uses NumPy."""
    if scale is not None and not 0 <= scale <= 22:
        return None
    out = get_kernel().lognormal_values(
        stream.k0, stream.k1, row_start, n_rows, mu, sigma, low, high, scale
    )
    return None if out is None else to_numpy(out).astype(np.float64, copy=False)


@dataclass(frozen=True, slots=True)
class PoolPiece:
    """An entry of ``pool`` (strings) picked uniformly per row from ``stream`` (word 0)."""

    pool: pa.Array
    stream: RowStream


@dataclass(frozen=True, slots=True)
class IntPiece:
    """An integer in ``[low, high)`` drawn uniformly per row from ``stream`` (word 0), in decimal
    zero-padded to ``width``; ``remap`` (``(from, to)``) writes ``to`` where the draw is
    ``from``."""

    stream: RowStream
    low: int
    high: int
    width: int = 0
    remap: tuple[int, int] | None = None


@dataclass(frozen=True, slots=True)
class ColumnPiece:
    """A column of the caller (strings, or ints zero-padded to ``width``); ``slug`` writes ASCII
    text in lower case with its spaces removed."""

    column: pa.Array
    width: int = 0
    slug: bool = False


def compose_strings(
    literals: Sequence[str],
    pieces: Sequence[PoolPiece | IntPiece | ColumnPiece],
    row_start: int,
    n_rows: int,
) -> pa.Array:
    """``literals[0] + piece[0] + literals[1] + ...`` in one native pass: the pool, number and
    address providers. A null in a column piece makes the row null."""
    specs: list[tuple[Any, ...]] = []
    for piece in pieces:
        if isinstance(piece, PoolPiece):
            specs.append(("pool", piece.pool, piece.stream.k0, piece.stream.k1))
        elif isinstance(piece, IntPiece):
            frm, to = piece.remap if piece.remap is not None else (None, None)
            specs.append(
                (
                    "int",
                    piece.stream.k0,
                    piece.stream.k1,
                    piece.low,
                    piece.high,
                    piece.width,
                    frm,
                    to,
                )
            )
        else:
            specs.append(("col", piece.column, piece.width, piece.slug))
    return _arrow(get_kernel().compose_strings(list(literals), specs, row_start, n_rows))


def pool_take(pool: pa.Array, indices: npt.NDArray[np.integer[Any]]) -> pa.Array:
    """``pool[indices]`` (a ``string`` array)."""
    idx = arrow_array(np.asarray(indices, dtype=np.int64), type=pa.int64())
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
