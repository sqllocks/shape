"""Show whether the live-heap interposer loads into this Python and sees known allocations."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

MIB = 2**20
CHILD = r"""
import ctypes, sys
import numpy as np
lib = ctypes.CDLL(None)
for name in ('rss_live_peak', 'rss_live_current', 'rss_live_reset_peak'):
    getattr(lib, name)  # AttributeError here means the dylib did not load
lib.rss_live_peak.restype = ctypes.c_int64
lib.rss_live_current.restype = ctypes.c_int64
def report(label, base):
    print(label, 'peak_delta_MiB', (lib.rss_live_peak() - base) // 2**20,
          'current_delta_MiB', (lib.rss_live_current() - base) // 2**20, flush=True)
lib.rss_live_reset_peak()
base = lib.rss_live_current()
a = np.empty(200 * 2**20, dtype=np.uint8); a[::4096] = 1
report('numpy-held', base)
del a
report('numpy-freed', base)
lib.rss_live_reset_peak()
b = bytearray(200 * 2**20); b[::4096] = b'x' * ((len(b) + 4095) // 4096)
report('bytearray-held', base)
del b
report('bytearray-freed', base)
"""


def run(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    result = subprocess.run(command, capture_output=True, text=True, **kwargs)
    print("$", " ".join(command), f"-> exit {result.returncode}", flush=True)
    print(result.stdout, end="", flush=True)
    print(result.stderr, end="", flush=True)
    return result


def main() -> int:
    dylib = Path(sys.argv[1]).resolve()
    smoke = Path(sys.argv[2]).resolve()
    real = os.path.realpath(sys.executable)
    print("sys.executable", sys.executable, "->", real, flush=True)
    run(["codesign", "-dvv", real])
    run(["codesign", "-d", "--entitlements", "-", real])
    run(["file", real])
    env = {**os.environ, "DYLD_INSERT_LIBRARIES": str(dylib), "PYTHONMALLOC": "malloc"}
    c = run([str(smoke)], env=env)
    py = run([sys.executable, "-c", CHILD], env=env)
    if c.returncode or py.returncode:
        print("FAIL: child did not run with the interposer", flush=True)
        return 1
    held = {}
    for line in py.stdout.splitlines():
        label, _, peak, _, current = line.split()[0], *line.split()[1:5]
        held[label] = (int(peak), int(current))
    print(held, flush=True)
    ok = (
        held["numpy-held"][0] >= 200
        and held["numpy-freed"][1] < 20
        and held["bytearray-held"][0] >= 200
        and held["bytearray-freed"][1] < 20
    )
    print("PASS" if ok else "FAIL: allocations not tracked as expected", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
