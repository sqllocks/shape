"""Scoped single-thread BLAS, for numpy work that runs beside a pool of Python worker threads.

A table's correlation is a few small matrix products. With the BLAS thread pool at full size
they wake every core and then spin, which takes the cores from the column workers running at
the same time (about 0.2 CPU-s of spinning on a small multi-table profile). Inside the scope
the BLAS pool is cut to one thread. The result of a product does not depend on the thread count.

The control is best effort: it needs OpenBLAS as bundled with numpy on Linux, and anywhere it is
not found the scope does nothing (the profile is the same, only slower).
"""

from __future__ import annotations

import ctypes
import re
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

_LOCK = threading.Lock()
_DEPTH = 0
_SAVED = 0
_CONTROL: tuple[Any, Any] | None = None
_PROBED = False
_SETTERS = ("scipy_openblas_set_num_threads64_", "openblas_set_num_threads64_")
_GETTERS = ("scipy_openblas_get_num_threads64_", "openblas_get_num_threads64_")


def _find_control() -> tuple[Any, Any] | None:
    """``(get_num_threads, set_num_threads)`` of the OpenBLAS already loaded in this process."""
    try:
        with open("/proc/self/maps", encoding="utf-8") as fh:
            maps = fh.read()
    except OSError:
        return None
    paths = dict.fromkeys(
        m.group(0) for m in re.finditer(r"/\S*openblas\S*\.so[^\s]*", maps, re.IGNORECASE)
    )
    for path in paths:
        try:
            lib = ctypes.CDLL(path)
        except OSError:
            continue
        for get_name, set_name in zip(_GETTERS, _SETTERS, strict=True):
            try:
                getter = getattr(lib, get_name)
                setter = getattr(lib, set_name)
            except AttributeError:
                continue
            getter.restype = ctypes.c_int
            setter.argtypes = [ctypes.c_int]
            setter.restype = None
            return getter, setter
    return None


@contextmanager
def single_thread_blas() -> Iterator[None]:
    """Run the body with the BLAS pool at one thread; concurrent scopes share one setting and
    the last one out restores the size found by the first one in."""
    global _DEPTH, _SAVED, _CONTROL, _PROBED
    with _LOCK:
        if not _PROBED:
            _CONTROL, _PROBED = _find_control(), True
        control = _CONTROL
        if control is not None:
            if _DEPTH == 0:
                _SAVED = int(control[0]())
                if _SAVED > 1:
                    control[1](1)
            _DEPTH += 1
    try:
        yield
    finally:
        if control is not None:
            with _LOCK:
                _DEPTH -= 1
                if _DEPTH == 0 and _SAVED > 1:
                    control[1](_SAVED)
