"""Shared helpers for the harness: the exclusive benchmark lock, the load gate, resource usage
and machine metadata (section 1.4 of the plan). Standard library only, on every platform."""

from __future__ import annotations

import contextlib
import os
import platform
import sys
import time
from collections.abc import Iterator
from types import ModuleType
from typing import IO, NamedTuple

from paths import BENCH_OUT_DIR

resource: ModuleType | None
try:
    import resource
except ImportError:  # Windows: no resource module (see rusage)
    resource = None

LOAD_MAX = 1.5
WINDOWS = os.name == "nt"


@contextlib.contextmanager
def bench_lock() -> Iterator[None]:
    """Hold ``$BENCH_OUT_DIR/bench.lock`` exclusively (``flock``; ``msvcrt.locking`` on Windows).
    Re-entrant across processes through ``BENCH_LOCK_HELD``: a parent that holds the lock exports
    it to its children."""
    if os.environ.get("BENCH_LOCK_HELD") == "1":
        yield
        return
    BENCH_OUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(BENCH_OUT_DIR / "bench.lock", "a") as fh:
        _lock(fh, exclusive=True)
        os.environ["BENCH_LOCK_HELD"] = "1"
        try:
            yield
        finally:
            os.environ.pop("BENCH_LOCK_HELD", None)
            _lock(fh, exclusive=False)


def _lock(fh: IO[str], *, exclusive: bool) -> None:
    """Take (or release) the whole-file lock on ``fh``, waiting for it as long as it takes."""
    if WINDOWS:
        import msvcrt  # noqa: PLC0415  (Windows only)

        fh.seek(0)  # the lock is on the first byte; locking past the end of a file is allowed
        if not exclusive:
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            return
        while True:
            try:  # LK_LOCK retries for about 10 seconds, then raises: keep waiting
                msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
                return
            except OSError:
                continue
    import fcntl  # noqa: PLC0415  (POSIX only)

    fcntl.flock(fh, fcntl.LOCK_EX if exclusive else fcntl.LOCK_UN)


def wait_for_quiet(limit: float = LOAD_MAX, max_wait_s: float = 900) -> float:
    """Wait until the 1-minute load average is <= limit (at most max_wait_s); return it."""
    waited = 0.0
    while True:
        load = os.getloadavg()[0] if hasattr(os, "getloadavg") else 0.0
        if load <= limit or waited >= max_wait_s:
            return load
        time.sleep(15)
        waited += 15


class Usage(NamedTuple):
    """CPU time (seconds), page faults and peak resident set size (KiB) of a process."""

    utime: float
    stime: float
    minflt: float
    majflt: float
    maxrss_kib: float


def rusage(children: bool = False) -> Usage:
    """The resource usage of this process (or of its waited-for children), on every platform.

    ``ru_maxrss`` is KiB on Linux but bytes on macOS. Windows has no ``resource`` module: CPU time
    comes from ``os.times()``, the page faults (minor and major together) and the peak working set
    from ``GetProcessMemoryInfo``; the children of a process are not measured there (NaN)."""
    if resource is not None:
        who = resource.RUSAGE_CHILDREN if children else resource.RUSAGE_SELF
        r = resource.getrusage(who)
        maxrss = float(r.ru_maxrss)
        return Usage(
            r.ru_utime,
            r.ru_stime,
            float(r.ru_minflt),
            float(r.ru_majflt),
            maxrss / 1024.0 if sys.platform == "darwin" else maxrss,
        )
    if children:
        nan = float("nan")
        return Usage(nan, nan, nan, nan, nan)
    times = os.times()
    peak_bytes, faults = windows_memory()
    return Usage(times.user, times.system, float(faults), 0.0, peak_bytes / 1024.0)


def windows_memory() -> tuple[int, int]:
    """``(PeakWorkingSetSize in bytes, PageFaultCount)`` of this process (Windows only)."""
    import ctypes  # noqa: PLC0415
    from ctypes import wintypes  # noqa: PLC0415

    class Counters(ctypes.Structure):
        _fields_ = [  # noqa: RUF012  (the ctypes layout of PROCESS_MEMORY_COUNTERS)
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    kernel32 = getattr(ctypes, "WinDLL")("kernel32", use_last_error=True)  # noqa: B009
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.K32GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(Counters),
        wintypes.DWORD,
    ]
    counters = Counters()
    counters.cb = ctypes.sizeof(Counters)
    if not kernel32.K32GetProcessMemoryInfo(
        kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb
    ):
        raise OSError(ctypes.get_last_error(), "GetProcessMemoryInfo failed")
    return int(counters.PeakWorkingSetSize), int(counters.PageFaultCount)


def maxrss_kib(children: bool = False) -> float:
    """Peak resident set size of this process (or of its largest waited-for child) in KiB."""
    return rusage(children).maxrss_kib


def peak_rss_mb() -> float:
    """Peak resident set size of this process in MB, on every platform."""
    return maxrss_kib() / 1024.0


def machine_meta() -> dict[str, object]:
    cpu = "?"
    with contextlib.suppress(OSError), open("/proc/cpuinfo") as fh:
        cpu = next((ln.split(":", 1)[1].strip() for ln in fh if ln.startswith("model name")), "?")
    return {
        "cores": os.cpu_count(),
        "cpu": cpu,
        "platform": platform.platform(),
        "python": platform.python_version(),
        "started": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
