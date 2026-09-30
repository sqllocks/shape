# ruff: noqa: E501
"""Temporary CI probe (lead): peak RSS of bounded profiling per Arrow memory pool, per OS.
Not for merge; lives only on branch ci/rss-macos-probe."""

import os
import subprocess
import sys

import numpy as np
import pyarrow as pa
import pyarrow.csv as pacsv

CODE = r"""
import sys, os
import pyarrow as pa
from shape.profile.engine import profile
profile(sys.argv[1], mode='bounded')
if sys.platform == 'win32':
    import ctypes
    from ctypes import wintypes
    class PMC(ctypes.Structure):
        _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD),
                    ('PeakWorkingSetSize', ctypes.c_size_t), ('WorkingSetSize', ctypes.c_size_t),
                    ('QuotaPeakPagedPoolUsage', ctypes.c_size_t), ('QuotaPagedPoolUsage', ctypes.c_size_t),
                    ('QuotaPeakNonPagedPoolUsage', ctypes.c_size_t), ('QuotaNonPagedPoolUsage', ctypes.c_size_t),
                    ('PagefileUsage', ctypes.c_size_t), ('PeakPagefileUsage', ctypes.c_size_t)]
    c = PMC(); c.cb = ctypes.sizeof(PMC)
    ctypes.windll.psapi.GetProcessMemoryInfo(ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(c), c.cb)
    peak = c.PeakWorkingSetSize
else:
    import resource
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    peak = peak if sys.platform == 'darwin' else peak * 1024
print(pa.default_memory_pool().backend_name, peak)
"""


def test_rss_probe(tmp_path):
    out = []
    files = {}
    for rows in (3_000_000, 21_000_000):
        rng = np.random.default_rng(1)
        p = tmp_path / f"r{rows}.csv"
        w = None
        for s in range(0, rows, 1_000_000):
            m = min(1_000_000, rows - s)
            t = pa.table(
                {
                    "id": pa.array(np.arange(s, s + m)),
                    "x": pa.array(rng.normal(size=m)),
                    "name": pa.array([f"n{k}" for k in rng.integers(0, 5000, m)]),
                }
            )
            w = w or pacsv.CSVWriter(p, t.schema)
            w.write_table(t)
        w.close()
        files[rows] = p
    for pool in ("", "system", "mimalloc", "jemalloc"):
        env = dict(os.environ)
        if pool:
            env["ARROW_DEFAULT_MEMORY_POOL"] = pool
        peaks = {}
        for rows, p in files.items():
            r = subprocess.run(
                [sys.executable, "-c", CODE, str(p)], capture_output=True, text=True, env=env
            )
            peaks[rows] = r.stdout.strip() or r.stderr.strip()[-200:]
        out.append(f"{sys.platform} pool={pool or 'default'}: {peaks}")
    raise AssertionError("PROBE\n" + "\n".join(out))
