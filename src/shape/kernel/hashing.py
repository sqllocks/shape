"""Canonical value hashing (T-13) through the selected kernel.

``hash_column`` hashes an Arrow array or chunked array with the native kernel (or the Python
reference under ``SHAPE_KERNEL=python``); ``hash_value`` hashes one Python scalar. Both agree
bit for bit, and both are deterministic across processes and platforms (never Python ``hash()``).
Null and NaN are excluded: they hash to ``None`` / a null slot.
"""

from __future__ import annotations

from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]

from .dispatch import get_kernel
from .reference.hashing import canonical_bytes as canonical_bytes
from .reference.hashing import hash_value as hash_value


def hash_column(array: Any, seed: int = 0) -> Any:
    """uint64 hashes of every element (null where the element is null or NaN)."""
    kernel = get_kernel()
    if isinstance(array, pa.ChunkedArray):
        chunks = [pa.array(kernel.hash_array(c, seed)) for c in array.chunks]
        return pa.chunked_array(chunks, type=pa.uint64())
    return pa.array(kernel.hash_array(array, seed))
