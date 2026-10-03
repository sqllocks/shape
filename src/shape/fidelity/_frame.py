"""A small typed view of an Arrow table for the fidelity tiers (numpy only, no pandas).

The tiers classify a column the way a data-frame library would: *number* (integer or float, not
boolean), *numeric* (a number or a boolean), *datetime* (a timestamp) and everything else
(*text*: strings, dates, decimals, ...). A float ``NaN`` is a missing value, like a null; an
integer column with nulls is read as floats, as a data frame does. Both are what the reference
implementation sees when it reads the same Parquet file, which is what the parity harness
checks (the parity harness under ``benchmarks/`` in the repository).
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
import numpy.typing as npt
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]

_NS_PER_UNIT = {"s": 10**9, "ms": 10**6, "us": 10**3, "ns": 1}


@dataclass(frozen=True, slots=True)
class Column:
    """One column. ``values`` holds ``int64`` (kind ``int``), ``float64`` (``float``), ``bool``
    (``bool``), ``int64`` nanoseconds since the epoch (``datetime``) or Python objects (``text``);
    ``valid`` is false where the value is missing."""

    name: str
    kind: str
    values: npt.NDArray[Any]
    valid: npt.NDArray[np.bool_]

    @property
    def is_number(self) -> bool:
        """An integer or float column (what ``numpy.number`` covers; not a boolean)."""
        return self.kind in ("int", "float")

    @property
    def is_numeric(self) -> bool:
        """A number or a boolean."""
        return self.kind in ("int", "float", "bool")

    @property
    def is_datetime(self) -> bool:
        return self.kind == "datetime"

    def __len__(self) -> int:
        return int(self.values.shape[0])

    def present(self) -> npt.NDArray[Any]:
        """The values that are not missing, in row order."""
        return self.values[self.valid]

    def floats(self) -> npt.NDArray[np.float64]:
        """The present values of a numeric column as ``float64``."""
        return self.present().astype(np.float64)

    def text(self) -> list[str]:
        """The present values as strings (``str()`` of each Python value)."""
        return [str(v) for v in self.present()]

    def codes(self) -> npt.NDArray[np.int64]:
        """Each row's rank among the sorted distinct values of ``str(value)``, a missing value
        being the text ``__NULL__``: the integer encoding of a categorical column."""
        strings = np.array(
            [str(v) if ok else "__NULL__" for v, ok in zip(self.values, self.valid, strict=True)]
        )
        return np.unique(strings, return_inverse=True)[1].astype(np.int64)

    def nunique(self) -> int:
        """Distinct present values."""
        v = self.present()
        if v.size == 0:
            return 0
        if self.kind == "text":
            return len(set(v.tolist()))
        return int(np.unique(v).size)


class Frame:
    """The columns of a table, in order."""

    def __init__(self, columns: Sequence[Column], n_rows: int) -> None:
        self.columns = list(columns)
        self.n_rows = n_rows
        self._by_name = {c.name: c for c in self.columns}

    def __len__(self) -> int:
        return self.n_rows

    def __contains__(self, name: object) -> bool:
        return name in self._by_name

    def __getitem__(self, name: str) -> Column:
        return self._by_name[name]

    def __iter__(self) -> Iterator[Column]:
        return iter(self.columns)

    @property
    def names(self) -> list[str]:
        return [c.name for c in self.columns]

    @property
    def numbers(self) -> list[Column]:
        """Integer and float columns (``numpy.number``), in column order."""
        return [c for c in self.columns if c.is_number]

    @classmethod
    def from_arrow(cls, table: pa.Table) -> Frame:
        # by position: a name may repeat (the last column of a name is the one looked up)
        return cls(
            [_column(f.name, table.column(i)) for i, f in enumerate(table.schema)],
            table.num_rows,
        )


def as_frame(data: pa.Table | Frame) -> Frame:
    """``data`` as a :class:`Frame` (an Arrow table is converted; a frame is returned as is)."""
    return data if isinstance(data, Frame) else Frame.from_arrow(data)


def _column(name: str, chunked: pa.ChunkedArray) -> Column:
    arr = chunked.combine_chunks() if chunked.num_chunks != 1 else chunked.chunk(0)
    t = arr.type
    if pa.types.is_dictionary(t):
        arr = arr.cast(t.value_type)
        t = arr.type
    nulls = arr.null_count
    if pa.types.is_boolean(t) and nulls == 0:
        v = arr.to_numpy(zero_copy_only=False).astype(bool)
        return Column(name, "bool", v, np.ones(len(v), dtype=bool))
    if pa.types.is_integer(t):
        if nulls == 0 and not pa.types.is_uint64(t):
            v = arr.to_numpy(zero_copy_only=False).astype(np.int64)
            return Column(name, "int", v, np.ones(len(v), dtype=bool))
        return _float_column(name, arr.cast(pa.float64()))
    if pa.types.is_floating(t):
        return _float_column(name, arr.cast(pa.float64()))
    if pa.types.is_timestamp(t):
        unit = t.unit
        ints = pc.cast(arr, pa.int64())
        valid = ~np.asarray(pc.is_null(ints).to_numpy(zero_copy_only=False), dtype=bool)
        v = ints.fill_null(0).to_numpy(zero_copy_only=False).astype(np.int64) * _NS_PER_UNIT[unit]
        return Column(name, "datetime", v, valid)
    objs = np.empty(len(arr), dtype=object)
    items = arr.to_pylist()
    for i, x in enumerate(items):
        objs[i] = x
    valid = np.fromiter((x is not None for x in items), dtype=bool, count=len(items))
    return Column(name, "text", objs, valid)


def _float_column(name: str, arr: pa.Array) -> Column:
    v = arr.fill_null(np.nan).to_numpy(zero_copy_only=False).astype(np.float64)
    return Column(name, "float", v, ~np.isnan(v))


def sample_positions(n: int, k: int) -> npt.NDArray[np.intp]:
    """The positions a seeded ``k``-row sample without replacement of ``n`` rows takes (row
    order is the draw order): numpy's legacy ``RandomState(0)`` permutation, the same draw a
    data-frame library's ``sample(k, random_state=0)`` makes."""
    rs = np.random.RandomState(0)
    return rs.choice(n, size=k, replace=False).astype(np.intp, copy=False)
