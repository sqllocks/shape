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

SNAP_EVERY = 1000
CHILD = r"""
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
def snap(label, full=False):
    rec = {'label': label, 'zones': zones()}
    if full:
        pid = str(os.getpid())
        rec['vmmap'] = tool(['vmmap', '-summary', pid], 6000)
    print(json.dumps(rec), flush=True)
original_entry = engine.table_entry
def entry(*args, **kwargs):
    snap('pre_table_entry', True)
    out = original_entry(*args, **kwargs)
    snap('post_table_entry', True)
    return out
engine.table_entry = entry
snap('start')
engine.profile(sys.argv[1], mode='bounded')
snap('post_profile', True)
"""


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


def test_child_code() -> str:
    """The committed test's child program, with per-zone statistics added to its stderr JSON."""
    import ast

    tree = ast.parse(Path("tests/profile/test_engine.py").read_text())
    function = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef)
        and n.name == "test_bounded_mode_memory_does_not_grow_with_rows"
    )
    node = next(
        n
        for n in function.body
        if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "code"
    )
    code = ast.literal_eval(node.value)
    for old, new in (
        (
            "    live = {n: 0 for n, _ in Malloc._fields_}\n",
            "    live = {n: 0 for n, _ in Malloc._fields_}\n"
            "    per_zone = []\n"
            "    lib.malloc_get_zone_name.argtypes = [c.c_void_p]\n"
            "    lib.malloc_get_zone_name.restype = c.c_char_p\n",
        ),
        (
            "        for n in live:\n            live[n] += getattr(zone, n)\n",
            "        name = (lib.malloc_get_zone_name(zones[index]) or b'?').decode()\n"
            "        per_zone.append({'name': name,"
            " **{n: getattr(zone, n) for n, _ in Malloc._fields_}})\n"
            "        for n in live:\n            live[n] += getattr(zone, n)\n",
        ),
        ("'malloc': live,", "'malloc': live, 'per_zone': per_zone,"),
        ("'final_mac': final_mac,", "'final_mac': final_mac, 'final_mac2': mac_snapshot(),"),
    ):
        assert code.count(old) == 1, old
        code = code.replace(old, new)
    return code


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--test-child", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--children", type=int, default=6)
    parser.add_argument("--rows", type=int, default=24_000_000)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    csv_path = args.output / "input.csv"
    write_csv(csv_path, args.rows)
    if args.test_child:
        code = test_child_code()
        for child in range(args.children):
            r = subprocess.run(
                [sys.executable, "-c", code, str(csv_path)], capture_output=True, text=True
            )
            (args.output / f"testchild{child}.stderr.json").write_text(r.stderr)
            (args.output / f"testchild{child}.stdout").write_text(r.stdout)
            print(f"testchild {child}: rc={r.returncode} stdout={r.stdout.strip()}", flush=True)
            line = next((x for x in r.stderr.splitlines() if x.startswith("{")), "{}")
            info = json.loads(line)
            for key in ("initial_mac", "final_mac", "final_mac2"):
                mac = info.get(key) or {}
                print(
                    f"  {key}: live={mac.get('malloc')} null_call={mac.get('malloc_all_zones_call')}"
                    f" per_zone={mac.get('per_zone')}",
                    flush=True,
                )
            print(
                f"  csv_arrow_peak={info.get('csv_arrow_peak')} arrow_peak={info.get('arrow_peak')}"
            )
        csv_path.unlink()
        return
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
            rec = json.loads(line)
            total = sum(z["max_size_in_use"] for z in rec["zones"].values())
            if "label" in rec:
                print(f"child {child} {rec['label']}: summed zone max_size_in_use={total}")
            peak = max(peak, total)
        print(f"child {child}: rc={r.returncode} summed zone max_size_in_use={peak}")
        if r.returncode:
            print(r.stderr[-2000:])
    csv_path.unlink()


if __name__ == "__main__":
    main()
