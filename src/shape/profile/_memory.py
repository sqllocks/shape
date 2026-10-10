"""Reclaim freed allocator pages during bounded profiling."""

from __future__ import annotations

import sys
from collections.abc import Callable
from functools import cache
from typing import Protocol, cast


class _MemoryPool(Protocol):
    @property
    def backend_name(self) -> str: ...

    def release_unused(self) -> None: ...


@cache
def _system_pressure_relief() -> Callable[[None, int], int] | None:
    if sys.platform != "darwin":
        return None
    import ctypes

    try:
        relief = ctypes.CDLL(None).malloc_zone_pressure_relief
    except (AttributeError, OSError):
        return None
    relief.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
    relief.restype = ctypes.c_size_t
    return cast(Callable[[None, int], int], relief)


def release_unused(pool: _MemoryPool) -> None:
    pool.release_unused()
    # Arrow's system allocator only implements ReleaseUnused with glibc's malloc_trim.
    # Ask Darwin's malloc zones to reclaim their freed pages too; NULL/0 covers all zones
    # and requests as much unused memory as possible. Live buffers remain allocated.
    if pool.backend_name == "system":
        relief = _system_pressure_relief()
        if relief is not None:
            relief(None, 0)
