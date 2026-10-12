"""Name the large system-malloc regions of a bounded CSV scan on macOS.

Each child profiles one CSV and, after every batch, records per-zone malloc statistics; every
SNAP_EVERY batches it also runs ``vmmap -summary`` and ``heap -sortBySize`` against itself.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.csv as pacsv

SNAP_EVERY = 8
CHILD = r'''
import ctypes as c, json, os, subprocess, sys
import pyarrow as pa
import shape.profile.engine as engine
pa.set_io_thread_count(1)
pa.set_cpu_count(1)
lib = c.CDLL('/usr/lib/libSystem.B.dylib', use_errno=True)
class Stats(c.Structure):
    _fields_ = [('blocks_in_use', c.c_uint)] + [(n, c.c_size_t) for n in
                ('size_in_use', 'max_size_in_use', 'size_allocated')]
lib.malloc_get_all_zones.argtypes = [c.c_uint32, c.c_void_p,
                                     c.POINTER(c.POINTER(c.c_size_t)), c.POINTER(c.c_uint)]
lib.malloc_get_all_zones.restype = c.c_int
lib.malloc_get_zone_name.argtypes = [c.c_void_p]
lib.malloc_get_zone_name.restype = c.c_char_p
lib.malloc_zone_statistics.argtypes = [c.c_void_p, c.POINTER(Stats)]
def zones():
    addrs = c.POINTER(c.c_size_t)()
    n = c.c_uint()
    assert lib.malloc_get_all_zones(0, None, c.byref(addrs), c.byref(n)) == 0
    out = {}
    for i in range(n.value):
        s = Stats()
        lib.malloc_zone_statistics(addrs[i], c.byref(s))
        name = lib.malloc_get_zone_name(addrs[i])
        out[(name or b'?').decode() + '#%d' % i] = {k: getattr(s, k) for k, _ in Stats._fields_}
    return out
def tool(args, limit):
    r = subprocess.run(args, capture_output=True, text=True, timeout=300)
    return {'rc': r.returncode, 'out': r.stdout[:limit], 'err': r.stderr[-500:]}
calls = [0]
original = engine.release_unused
def traced(pool):
    original(pool)
    calls[0] += 1
    rec = {'batch': calls[0], 'zones': zones()}
    if calls[0] % int(sys.argv[2]) == 0:
        pid = str(os.getpid())
        rec['vmmap'] = tool(['vmmap', '-summary', pid], 6000)
        rec['heap'] = tool(['heap', pid, '-sortBySize'], 3000)
    print(json.dumps(rec), flush=True)
engine.release_unused = traced
engine.profile(sys.argv[1], mode='bounded')
'''


def write_csv(path: Path, rows: int) -> None:
    rng = np.random.default_rng(1)
    with pa.OSFile(str(path), "wb") as sink:
        writer = None
        for start in range(0, rows, 1_000_000):
            m = min(1_000_000, rows - start)
            chunk = pa.table(
                {
                    "id": pa.array(np.arange(start, start + m)),
                    "x": pa.array(rng.normal(size=m)),
                    "name": pa.array([f"n{k}" for k in rng.integers(0, 5000, m)]),
                }
            )
            if writer is None:
                writer = pacsv.CSVWriter(sink, chunk.schema)
            writer.write_table(chunk)
        assert writer is not None
        writer.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--children", type=int, default=6)
    parser.add_argument("--rows", type=int, default=24_000_000)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    csv_path = args.output / "input.csv"
    write_csv(csv_path, args.rows)
    for child in range(args.children):
        log = args.output / f"child{child}.jsonl"
        with log.open("w") as stream:
            r = subprocess.run(
                [sys.executable, "-c", CHILD, str(csv_path), str(SNAP_EVERY)],
                stdout=stream,
                stderr=subprocess.PIPE,
                text=True,
            )
        peak = 0
        for line in log.read_text().splitlines():
            peak = max(peak, sum(z["max_size_in_use"] for z in json.loads(line)["zones"].values()))
        print(f"child {child}: rc={r.returncode} summed zone max_size_in_use={peak}")
        if r.returncode:
            print(r.stderr[-2000:])
    csv_path.unlink()


if __name__ == "__main__":
    main()
