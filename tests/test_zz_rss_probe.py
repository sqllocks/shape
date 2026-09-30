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
try:
    import resource
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    peak = peak if sys.platform == 'darwin' else peak * 1024
except ImportError:
    import psutil
    peak = psutil.Process().memory_info().peak_wset
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
            t = pa.table({"id": pa.array(np.arange(s, s + m)), "x": pa.array(rng.normal(size=m)),
                          "name": pa.array([f"n{k}" for k in rng.integers(0, 5000, m)])})
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
            r = subprocess.run([sys.executable, "-c", CODE, str(p)], capture_output=True,
                               text=True, env=env)
            peaks[rows] = (r.stdout.strip() or r.stderr.strip()[-200:])
        out.append(f"{sys.platform} pool={pool or 'default'}: {peaks}")
    raise AssertionError("PROBE\n" + "\n".join(out))
