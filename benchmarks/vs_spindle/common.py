"""Shared helpers for the harness: the exclusive benchmark lock, the load gate and machine
metadata (section 1.4 of the plan). Standard library only."""

from __future__ import annotations

import contextlib
import os
import platform
import time
from collections.abc import Iterator

from paths import BENCH_OUT_DIR

LOAD_MAX = 1.5


@contextlib.contextmanager
def bench_lock() -> Iterator[None]:
    """Hold ``$BENCH_OUT_DIR/bench.lock`` exclusively (flock). Re-entrant across processes
    through ``BENCH_LOCK_HELD``: a parent that holds the lock exports it to its children."""
    if os.environ.get("BENCH_LOCK_HELD") == "1" or os.name == "nt":
        yield
        return
    import fcntl

    BENCH_OUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(BENCH_OUT_DIR / "bench.lock", "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        os.environ["BENCH_LOCK_HELD"] = "1"
        try:
            yield
        finally:
            os.environ.pop("BENCH_LOCK_HELD", None)
            fcntl.flock(fh, fcntl.LOCK_UN)


def wait_for_quiet(limit: float = LOAD_MAX, max_wait_s: float = 900) -> float:
    """Wait until the 1-minute load average is <= limit (at most max_wait_s); return it."""
    waited = 0.0
    while True:
        load = os.getloadavg()[0] if hasattr(os, "getloadavg") else 0.0
        if load <= limit or waited >= max_wait_s:
            return load
        time.sleep(15)
        waited += 15


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
