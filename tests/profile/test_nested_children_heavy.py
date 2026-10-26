"""W9-05: a three-level file larger than RAM used by its bounded child scan."""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pyarrow as pa
import pyarrow.parquet as pq
import pytest


@pytest.mark.heavy
@pytest.mark.parametrize("kernel", ["rust", "python"])
def test_gigabyte_nested_file_uses_bounded_child_state(tmp_path, kernel):
    path = tmp_path / "nested.parquet"
    batch = pa.table({"outer": [{"items": [{"payload": "x" * 8192}]}] * 1024})
    with pq.ParquetWriter(path, batch.schema, compression=None, use_dictionary=False) as writer:
        for _ in range(128):
            writer.write_table(batch)
    assert path.stat().st_size >= 1_000_000_000
    code = """
import json, sys
import pyarrow as pa
from shape.profile.engine import EngineOptions, profile_table
pa.set_cpu_count(1)
pa.set_io_thread_count(1)
def peak():
    if sys.platform == 'win32':
        import ctypes
        from ctypes import wintypes as w
        class Counters(ctypes.Structure):
            names = ('peak', 'ws', 'a', 'b', 'c', 'd', 'pf', 'ppf')
            _fields_ = [('cb', w.DWORD), ('faults', w.DWORD)]
            _fields_ += [(n, ctypes.c_size_t) for n in names]
        c = Counters(); c.cb = ctypes.sizeof(c)
        k = ctypes.windll.kernel32; k.GetCurrentProcess.restype = w.HANDLE
        p = ctypes.windll.psapi
        p.GetProcessMemoryInfo.argtypes = [w.HANDLE, ctypes.c_void_p, w.DWORD]
        p.GetProcessMemoryInfo(k.GetCurrentProcess(), ctypes.byref(c), c.cb)
        return c.peak
    import resource
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return value if sys.platform == 'darwin' else value * 1024
before = peak()
doc = profile_table(sys.argv[1], options=EngineOptions(mode='bounded', batch_size=256, threads=1))
child = doc['columns'][0]['children'][0]['children'][0]['children'][0]
assert child['path'] == '$.outer.items[].payload'
assert child['row_count'] == 131072
assert child['length']['mean'] == 8192
assert child['error_models']['cardinality']['exact'] is False
print(json.dumps({'growth': peak() - before, 'rows': doc['rows']}))
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(path)],
        env={**os.environ, "SHAPE_KERNEL": kernel},
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stderr
    measured = json.loads(result.stdout)
    assert measured["rows"] == 131072
    assert measured["growth"] < 256 * 2**20, measured
