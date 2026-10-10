"""Scratch-only diagnostic: exact bounded test child, plus end-of-process counters.

Run: python scripts/diag_macos_memory.py --output diagnostics --runs 10
Never merge this diagnostic branch into the release branch.
"""

import argparse
import ast
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.csv as pacsv

EXTRA = r'''
import ctypes, os, subprocess
libc = ctypes.CDLL(None)
class BasicInfo(ctypes.Structure):
    _fields_ = [('virtual_size', ctypes.c_uint64), ('resident_size', ctypes.c_uint64),
        ('resident_size_max', ctypes.c_uint64), ('user', ctypes.c_int32 * 2),
        ('system', ctypes.c_int32 * 2), ('policy', ctypes.c_int32),
        ('suspend', ctypes.c_int32)]
info = BasicInfo()
count = ctypes.c_uint32(ctypes.sizeof(info) // 4)
libc.task_info.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
    ctypes.POINTER(ctypes.c_uint32)]
libc.task_info.restype = ctypes.c_int
task = ctypes.c_uint32.in_dll(libc, 'mach_task_self_').value
status = libc.task_info(task, 20, ctypes.byref(info), ctypes.byref(count))
assert status == 0, status
class MallocStats(ctypes.Structure):
    _fields_ = [('blocks_in_use', ctypes.c_uint32), ('size_in_use', ctypes.c_size_t),
        ('max_size_in_use', ctypes.c_size_t), ('size_allocated', ctypes.c_size_t)]
stats = MallocStats()
libc.malloc_zone_statistics.argtypes = [ctypes.c_void_p, ctypes.POINTER(MallocStats)]
libc.malloc_zone_statistics(None, ctypes.byref(stats))
result = {'peak_rss': peak, 'current_rss': info.resident_size,
    'initial_peak': initial_peak, 'arrow_pool': pool.backend_name,
    'arrow_peak': pool.max_memory(), 'arrow_bytes': pool.bytes_allocated(),
    'malloc': {name: getattr(stats, name) for name, _ in stats._fields_},
    'rust_allocation_counters': 'not exposed by the kernel'}
print('DIAGNOSTIC ' + json.dumps(result), flush=True)
vmmap = subprocess.run(['/usr/bin/vmmap', '--summary', str(os.getpid())],
    capture_output=True, text=True)
print('VMMAP_STATUS', vmmap.returncode)
print(vmmap.stdout)
print(vmmap.stderr, file=sys.stderr)
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--runs', type=int, default=10)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    root = Path(__file__).resolve().parents[1]
    tree = ast.parse((root / 'tests/profile/test_engine.py').read_text(encoding='utf-8'))
    test = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
        and n.name == 'test_bounded_mode_memory_does_not_grow_with_rows')
    assignment = next(n for n in test.body if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == 'code' for t in n.targets))
    child = ast.literal_eval(assignment.value) + EXTRA
    datasets = {}
    for rows in (24_000_000, 48_000_000):
        path = args.output / f'r{rows}.csv'
        rng = np.random.default_rng(1)
        with pa.OSFile(str(path), 'wb') as sink:
            writer = None
            for start in range(0, rows, 1_000_000):
                m = min(1_000_000, rows - start)
                chunk = pa.table({'id': pa.array(np.arange(start, start + m)),
                    'x': pa.array(rng.normal(size=m)),
                    'name': pa.array([f'n{k}' for k in rng.integers(0, 5000, m)])})
                if writer is None:
                    writer = pacsv.CSVWriter(sink, chunk.schema)
                writer.write_table(chunk)
            writer.close()
        datasets[rows] = path
    results = []
    try:
        for iteration in range(1, args.runs + 1):
            pair = {'run': iteration}
            for rows, path in datasets.items():
                process = subprocess.run([sys.executable, '-c', child, str(path)],
                    capture_output=True, text=True, timeout=1800)
                (args.output / f'run{iteration}-{rows}.txt').write_text(
                    process.stdout + '\nSTDERR\n' + process.stderr, encoding='utf-8')
                if process.returncode:
                    raise RuntimeError(process.stderr)
                result = json.loads(next(line.removeprefix('DIAGNOSTIC ')
                    for line in process.stdout.splitlines() if line.startswith('DIAGNOSTIC ')))
                pair[str(rows)] = result
            small, large = (pair[str(rows)]['peak_rss'] for rows in datasets)
            pair['absolute_difference_pct'] = abs(large - small) / small * 100
            pair['growth_pct'] = (large - small) / small * 100
            results.append(pair)
            (args.output / 'results.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
            print(json.dumps(pair), flush=True)
    finally:
        for path in datasets.values():
            path.unlink(missing_ok=True)
    print('PASS_PAIRS', sum(p['absolute_difference_pct'] < 10 for p in results), '/', len(results))


if __name__ == '__main__':
    main()
