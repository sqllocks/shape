"""Measure the keyed-state scale workload's memory growth on macOS in several ways.

Runs the committed test's child program (10**8 events, 10**6 keys) several times, with and
without the live-heap interposer, and prints for the checkpoint (10**7 events) and the end:
the test's own metric (resident size after gc and release_unused), physical footprint, the
interposer's live and peak heap bytes, and the state sizes.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import time
from pathlib import Path

SNAP = """
def snap(label):
    import ctypes, os
    lib = ctypes.CDLL(None)
    rec = {'label': label, 'events': done}
    for name in ('rss_live_current', 'rss_live_peak'):
        fn = getattr(lib, name, None)
        if fn is not None:
            fn.restype = ctypes.c_int64
            rec[name] = fn()
    proc = ctypes.CDLL('/usr/lib/libproc.dylib', use_errno=True)
    class Usage(ctypes.Structure):
        _fields_ = [('uuid', ctypes.c_ubyte * 16), ('values', ctypes.c_uint64 * 35)]
    proc.proc_pid_rusage.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
    usage = Usage()
    proc.proc_pid_rusage(os.getpid(), 4, ctypes.byref(usage))
    rec['resident_size'] = usage.values[6]
    rec['phys_footprint'] = usage.values[7]
    rec['lifetime_max_phys_footprint'] = usage.values[28]
    print('SNAP ' + json.dumps(rec), file=sys.stderr, flush=True)
"""


def scale_source(ref: str) -> str:
    text = subprocess.check_output(
        ["git", "show", f"{ref}:tests/streaming/test_keyed_state.py"], text=True
    )
    for node in ast.parse(text).body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "_SCALE":
            code = ast.literal_eval(node.value)
            break
    else:
        raise RuntimeError("_SCALE not found")
    for old, new in (
        ("def state_bytes():", SNAP + "\ndef state_bytes():"),
        (
            "        at_checkpoint = resident_rss()\n",
            "        at_checkpoint = resident_rss()\n        snap('checkpoint')\n",
        ),
        (
            "print(at_checkpoint, resident_rss(), len(sketches), len(dedupe), kept)",
            "final_resident = resident_rss()\nsnap('final')\n"
            "print(at_checkpoint, final_resident, len(sketches), len(dedupe), kept)",
        ),
    ):
        assert code.count(old) == 1, old
        code = code.replace(old, new)
    compile(code, "<keyed child>", "exec")
    return code


def main() -> int:
    ref, out = sys.argv[1], Path(sys.argv[2])
    plain, instrumented = int(sys.argv[3]), int(sys.argv[4])
    out.mkdir(parents=True, exist_ok=True)
    code = scale_source(ref)
    dylib = out / "live_heap_peak.dylib"
    subprocess.run(
        ["clang", "-dynamiclib", "-O2", "-o", str(dylib), "tests/profile/live_heap_peak_darwin.c"],
        check=True,
    )
    env_live = {**os.environ, "DYLD_INSERT_LIBRARIES": str(dylib), "PYTHONMALLOC": "malloc"}
    rows = []
    for index, (label, env) in enumerate(
        [("plain", None)] * plain + [("live-heap", env_live)] * instrumented
    ):
        started = time.time()
        r = subprocess.run(
            [sys.executable, "-c", code, str(10**8), str(10**6), str(2**20), str(10**7)],
            capture_output=True,
            text=True,
            env=env,
        )
        (out / f"child{index}-{label}.stderr").write_text(r.stderr)
        snaps = {}
        for line in r.stderr.splitlines():
            if line.startswith("SNAP "):
                rec = json.loads(line[5:])
                snaps[rec["label"]] = rec
        print(
            f"--- child {index} ({label}) rc={r.returncode} {time.time() - started:.0f}s",
            flush=True,
        )
        if r.returncode or len(snaps) != 2:
            print(r.stderr[-3000:], flush=True)
            continue
        c, f = snaps["checkpoint"], snaps["final"]
        at_ten_million, final = (int(x) for x in r.stdout.split()[:2])
        row = {
            "label": label,
            "test_metric_checkpoint": at_ten_million,
            "test_metric_final": final,
            "test_metric_growth": round(final / at_ten_million - 1, 4),
        }
        for key in (
            "phys_footprint",
            "lifetime_max_phys_footprint",
            "rss_live_current",
            "rss_live_peak",
        ):
            if key in c:
                row[key] = [c[key], f[key], round(f[key] / c[key] - 1, 4)]
        rows.append(row)
        print(json.dumps(row), flush=True)
        for line in r.stderr.splitlines():
            if "state:" in line:
                print("   ", line[:400], flush=True)
    (out / "rows.json").write_text(json.dumps(rows, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
