"""Allocator selection for serial streaming CSV buffers."""

from __future__ import annotations

from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]


def streaming_csv_pool(use_threads: bool) -> Any:
    """Use a pool that can return completed streaming buffers to the operating system.

    The system allocator can keep freed buffers resident in large reusable arenas. Those
    pages still contribute to peak RSS even after Arrow has released every buffer. Select
    the dedicated mimalloc pool when available without changing the process's default pool.
    """
    if not use_threads and "mimalloc" in pa.supported_memory_backends():
        return pa.mimalloc_memory_pool()
    return None
