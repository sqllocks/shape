"""Process settings that make generation faster without changing any value it produces.

:func:`generation_memory` runs a block with Arrow's *system* allocator (``malloc``) as the default
memory pool. Arrow's default pool (mimalloc) maps fresh memory for every large array and hands it
back to the operating system soon after it is freed. A run that makes tens of megabytes of short
lived arrays on several threads then spends much of its time faulting those pages in again (on a
virtual machine the cost of a fault, and the lock the faults share with ``mmap``, can double the run
time), where ``malloc`` reuses the memory it already holds. Arrays made inside the block stay valid
after it, whichever pool owns them.

The first block in a process also raises glibc's allocation thresholds
(``shape._process.tune_malloc``), for the same reason: freed memory is kept and reused instead of
returned and faulted in again.

``SHAPE_MEMORY_POOL=default`` turns the switch off. Nested and concurrent uses share one switch: the
first to enter makes it, the last to leave undoes it.

Stable interface: ``generation_memory`` and ``MEMORY_POOL_ENV``.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]

from shape._process import tune_malloc

MEMORY_POOL_ENV = "SHAPE_MEMORY_POOL"

_lock = threading.Lock()
_depth = 0
_previous: Any = None


@contextmanager
def generation_memory() -> Iterator[None]:
    """Use Arrow's system memory pool inside the block (see the module docstring)."""
    global _depth, _previous
    if os.environ.get(MEMORY_POOL_ENV, "").strip().lower() == "default":
        yield
        return
    tune_malloc()  # once per process; see shape._process
    with _lock:
        if _depth == 0:
            _previous = pa.default_memory_pool()
            if _previous.backend_name != "system":
                pa.set_memory_pool(pa.system_memory_pool())
        _depth += 1
    try:
        yield
    finally:
        with _lock:
            _depth -= 1
            if _depth == 0:
                if _previous.backend_name != "system":
                    pa.set_memory_pool(_previous)
                _previous = None
