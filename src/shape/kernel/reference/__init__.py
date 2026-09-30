"""Pure-Python reference twin of the native kernel (T-03).

Every function of ``shape._kernel`` has a twin here with the same name, arguments and result.
It is the correctness oracle for the differential tests and the fallback when the native
extension is missing (for example in the pure ``py3-none-any`` wheel, T-29).
"""

from __future__ import annotations

from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]

from .hashing import hash_array as hash_array
from .sketch import Hll as Hll
from .sketch import Kll as Kll
from .sketch import SpaceSaving as SpaceSaving

NAME = "python"


def version() -> str:
    """The kernel version (equal to the Python package version)."""
    from shape import __version__

    return __version__


def _as_batch(batch: Any) -> pa.RecordBatch:
    if isinstance(batch, pa.RecordBatch):
        return batch
    return pa.record_batch(batch)  # any object with __arrow_c_array__


def roundtrip_batch(batch: Any) -> pa.RecordBatch:
    """Import a record batch and hand it back. Nothing is copied."""
    return _as_batch(batch)


def buffer_addresses(batch: Any) -> list[int]:
    """Address of every data buffer of every column, in column order (nulls first, then
    values, then child buffers), skipping absent buffers."""
    out: list[int] = []
    for column in _as_batch(batch).columns:
        out.extend(b.address for b in column.buffers() if b is not None)
    return out


def num_rows(batch: Any) -> int:
    """Row count of a record batch."""
    return int(_as_batch(batch).num_rows)
