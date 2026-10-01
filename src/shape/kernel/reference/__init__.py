"""Pure-Python reference twin of the native kernel (T-03).

Every function of ``shape._kernel`` has a twin here with the same name, arguments and result.
It is the correctness oracle for the differential tests and the fallback when the native
extension is missing (for example in the pure ``py3-none-any`` wheel, T-29).
"""

from __future__ import annotations

from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]

from .exact import count_numeric as count_numeric
from .exact import date_iso as date_iso
from .exact import float_repr as float_repr
from .exact import numeric_stats as numeric_stats
from .exact import round6 as round6
from .exact import temporal_counts as temporal_counts
from .exact import top_indices as top_indices
from .exact import value_counts_str as value_counts_str
from .fit import fit_distribution as fit_distribution
from .gen import alias_build as alias_build
from .gen import alias_sample as alias_sample
from .gen import day_weights as day_weights
from .gen import hour_weights_peaks as hour_weights_peaks
from .gen import join_strings as join_strings
from .gen import philox_normal as philox_normal
from .gen import philox_uniform as philox_uniform
from .gen import philox_words as philox_words
from .gen import pool_take as pool_take
from .gen import random_strings as random_strings
from .gen import string_case as string_case
from .gen import template_strings as template_strings
from .gen import temporal_sample as temporal_sample
from .gen import uuid4_strings as uuid4_strings
from .hashing import hash_array as hash_array
from .profile import ProfileState as ProfileState
from .relational import cap_per_parent as cap_per_parent
from .relational import first_flags as first_flags
from .relational import group_order as group_order
from .relational import scd2_offsets as scd2_offsets
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


def set_threads(n: int = 0) -> int:
    """The reference kernel is single-threaded; reports 1."""
    return 1
