"""Process-wide settings that change how fast Shape runs and never a value it produces.

:func:`configure` is called once, by ``shape/__init__``, so the command line and the Python API get
the same settings from the one place. It imports nothing heavy (``import shape`` stays free).

Arrow's default memory pool (mimalloc) reserves a large arena with ``MADV_HUGEPAGE`` on first use.
On a virtual machine whose transparent huge pages are in ``madvise`` mode that costs 10 to 13 ms of
system time in about half of all processes, and its habit of handing memory back to the system
soon after it is freed makes the short-lived arrays of a run fault their pages in again and again.
Arrow's system pool (``malloc``) has neither effect. Two cases:

* ``pyarrow`` is not imported yet (``shape`` was imported first, as the command line does):
  ``ARROW_DEFAULT_MEMORY_POOL=system`` is set, so every Arrow user in the process, the Parquet
  encoder included, takes the system pool. A value the environment already has is kept.
* ``pyarrow`` is already imported: its default pool is fixed, so the arena's huge pages are
  switched off for the process (``prctl(PR_SET_THP_DISABLE)``, Linux), which removes the stall and
  most of the page-fault cost; ``generation_memory`` still gives generation the system pool.

``SHAPE_MEMORY_POOL=default`` turns all of this off. They are the settings that the
``ARROW_DEFAULT_MEMORY_POOL`` environment variable and ``prctl`` offer to anyone; the effect is
measured in ``docs/GENERATION_ENGINE.md``.

:func:`tune_malloc` is the one setting that is not applied at import: the first generation in the
process calls it (``generation_memory``). glibc returns memory to the system soon after it is freed
(a trim threshold of 128 KiB that follows the largest block freed) and maps every large block fresh,
so the short-lived arrays of a run, on several threads, fault their pages in again and take the
lock the faults share with ``mmap``. Raising the three thresholds together (blocks up to 32 MiB
come from the heap and are reused, up to 256 MiB of free memory is kept, 16 MiB is requested at a
time) took 3 to 10% off the medium workloads, with no change in any value. The thresholds are set
together because setting one turns glibc's adaptive threshold off for good. A host that already
tunes ``malloc`` (``MALLOC_*`` variables, ``GLIBC_TUNABLES``) is left alone, and
``SHAPE_MEMORY_POOL=default`` turns it off.

Stable interface: ``configure`` and ``tune_malloc``.
"""

from __future__ import annotations

import os
import sys

MEMORY_POOL_ENV = "SHAPE_MEMORY_POOL"
ARROW_POOL_ENV = "ARROW_DEFAULT_MEMORY_POOL"
_PR_SET_THP_DISABLE = 41


def _disable_huge_pages() -> bool:
    """Switch off transparent huge pages for this process (Linux). True when it took effect."""
    if not sys.platform.startswith("linux"):
        return False
    try:
        import ctypes

        libc = ctypes.CDLL(None, use_errno=True)
        return bool(libc.prctl(_PR_SET_THP_DISABLE, 1, 0, 0, 0) == 0)
    except (OSError, AttributeError):
        return False


def configure() -> str:
    """Apply the settings above; return what was done: ``"off"`` (``SHAPE_MEMORY_POOL=default``),
    ``"env"`` (the Arrow system pool is selected for this process), ``"kept"`` (the environment
    already chose a pool), ``"thp"`` (pyarrow was imported first: huge pages are switched off) or
    ``"none"`` (nothing to do or not possible). Safe to call again."""
    if os.environ.get(MEMORY_POOL_ENV, "").strip().lower() == "default":
        return "off"
    if "pyarrow" not in sys.modules:
        if ARROW_POOL_ENV in os.environ:
            return "kept"
        os.environ[ARROW_POOL_ENV] = "system"
        return "env"
    pa = sys.modules["pyarrow"]
    try:
        backend = pa.default_memory_pool().backend_name
    except AttributeError:
        return "none"
    if backend == "system":
        return "none"
    return "thp" if _disable_huge_pages() else "none"


_M_TRIM_THRESHOLD, _M_TOP_PAD, _M_MMAP_THRESHOLD = -1, -2, -3
_MALLOC_SETTINGS = (
    (_M_MMAP_THRESHOLD, 32 << 20),  # the most mallopt accepts
    (_M_TRIM_THRESHOLD, 256 << 20),
    (_M_TOP_PAD, 16 << 20),
)
_malloc_tuned: str | None = None


def tune_malloc() -> str:
    """Raise glibc's allocation thresholds for the rest of the process (see the module docstring).
    Returns ``"tuned"``, ``"off"`` (``SHAPE_MEMORY_POOL=default``), ``"kept"`` (the environment
    tunes malloc already) or ``"none"`` (not glibc on Linux, or mallopt refused). Done once; later
    calls return the first answer."""
    global _malloc_tuned
    if _malloc_tuned is not None:
        return _malloc_tuned
    _malloc_tuned = _tune_malloc()
    return _malloc_tuned


def _tune_malloc() -> str:
    if os.environ.get(MEMORY_POOL_ENV, "").strip().lower() == "default":
        return "off"
    if "malloc" in os.environ.get("GLIBC_TUNABLES", "") or any(
        k.startswith("MALLOC_") for k in os.environ
    ):
        return "kept"
    if not sys.platform.startswith("linux"):
        return "none"
    try:
        import ctypes

        mallopt = ctypes.CDLL(None, use_errno=True).mallopt  # absent on musl
    except (OSError, AttributeError):
        return "none"
    return "tuned" if all(mallopt(code, value) == 1 for code, value in _MALLOC_SETTINGS) else "none"
