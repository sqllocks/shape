"""LEARN-CLI: the two ``learn`` command lines on D2, end to end (plan section 3.4, T-19).

    source scripts/env.sh && "$SHAPE_VENV/bin/python" \\
        benchmarks/vs_refengine/learn_1to1/bench_cli.py --runs 5 --warmup 1

Each timed run is a fresh process of

    $REFENGINE_VENV/bin/refengine learn $BENCH_DATA_DIR/profile/d2.csv -o OUT.refengine.json
    $SHAPE_VENV/bin/shape     learn $BENCH_DATA_DIR/profile/d2.csv -o OUT.json

(wall clock around the whole process: interpreter start, imports, reading the 1,000,000-row CSV,
profiling, building and writing the schema). Equivalence comes first: after the warm-up runs the two
outputs are compared field by field (``compare.py``); unexplained differences abort the run before
any time is recorded, and the last timed run's outputs are compared again. Default threading for
both. The run holds the exclusive benchmark lock, each run waits for a 1-minute load average
<= 1.5, and the output files are removed before every run. Tools are interleaved run by run; every
raw run, the median, the CPU model and its clock are written to the report.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import _refpkg  # noqa: E402
from common import bench_lock, machine_meta, rusage, wait_for_quiet  # noqa: E402
from compare import compare  # noqa: E402
from paths import BENCH_OUT_DIR, PROFILE_DATA_DIR, REFENGINE_VENV, SHAPE_VENV  # noqa: E402


def command(tool: str, src: Path, out: Path) -> list[str]:
    exe = (
        REFENGINE_VENV / "bin" / _refpkg.CONSOLE
        if tool == "refengine"
        else SHAPE_VENV / "bin" / "shape"
    )
    return [str(exe), "learn", str(src), "-o", str(out)]


def cpu_mhz() -> float | None:
    try:
        with open("/proc/cpuinfo") as fh:
            return float(next(ln.split(":")[1] for ln in fh if ln.startswith("cpu MHz")))
    except (OSError, StopIteration, ValueError):
        return None


def run_once(tool: str, src: Path, out: Path, threads: int | None = None) -> dict:
    out.unlink(missing_ok=True)  # no cache between runs
    load = wait_for_quiet()
    cmd = command(tool, src, out)
    env = dict(os.environ)
    if threads is not None and tool != "refengine":
        env["SHAPE_THREADS"] = str(threads)
    before = rusage(children=True)
    t0 = time.perf_counter()
    done = subprocess.run(cmd, capture_output=True, text=True, cwd=BENCH_OUT_DIR, env=env)
    wall = time.perf_counter() - t0
    after = rusage(children=True)
    if done.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} failed ({done.returncode}):\n{done.stderr[-2000:]}")
    return {
        "wall_s": wall,
        "cpu_s": (after.utime + after.stime) - (before.utime + before.stime),
        "loadavg_before": load,
        "cmd": " ".join(cmd),
    }


def verify(outs: dict[str, Path]) -> list[dict[str, str]]:
    """Equivalence: the two schemas written by the CLIs, field by field. Raises on a difference
    that is not a named deliberate one."""
    bad, explained = compare(
        json.loads(outs["shape"].read_text()), json.loads(outs["refengine"].read_text())
    )
    if bad:
        raise SystemExit("equivalence FAILED, timings do not count:\n" + "\n".join(bad))
    return explained


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dataset", default="d2.csv")
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--warmup", type=int, default=1)
    ap.add_argument(
        "--shape-threads", type=int, default=None, help="SHAPE_THREADS for the Shape runs"
    )
    ap.add_argument("--report", default=None)
    a = ap.parse_args(argv)
    src = PROFILE_DATA_DIR / a.dataset
    work = BENCH_OUT_DIR / "learn_cli"
    work.mkdir(parents=True, exist_ok=True)
    outs = {"refengine": work / "out.refengine.json", "shape": work / "out.shape.json"}
    report = Path(a.report) if a.report else BENCH_OUT_DIR / "bench" / "learn_cli.json"
    tools = ["refengine", "shape"]
    raw: dict[str, list[dict]] = {t: [] for t in tools}
    with bench_lock():
        meta = {
            **machine_meta(),
            "cpu_mhz": cpu_mhz(),
            "dataset": str(src),
            "runs": a.runs,
            "warmup": a.warmup,
            "shape_threads": a.shape_threads,
            "loadavg_start": os.getloadavg()[0],
        }
        for i in range(a.warmup):
            for t in tools:
                r = run_once(t, src, outs[t], a.shape_threads)
                print(f"{t:8s} warm-up {i + 1}: {r['wall_s']:.3f}s", flush=True)
        explained = verify(outs)  # equivalence first
        print(f"equivalence: equal except {len(explained)} listed columns", flush=True)
        for i in range(a.runs):
            for t in tools:
                r = run_once(t, src, outs[t], a.shape_threads)
                raw[t].append(r)
                print(
                    f"{t:8s} run {i + 1}: {r['wall_s']:.3f}s  cpu {r['cpu_s']:.2f}s  "
                    f"load {r['loadavg_before']:.2f}",
                    flush=True,
                )
        explained = verify(outs)  # and on the last timed outputs
    sp = statistics.median(r["wall_s"] for r in raw["refengine"])
    sh = statistics.median(r["wall_s"] for r in raw["shape"])
    out = {
        "meta": meta,
        "gate": "LEARN-CLI >= 10x (D2 csv, median of 5 fresh-process runs)",
        "refengine_median_s": sp,
        "shape_median_s": sh,
        "ratio": sp / sh,
        "refengine_runs_s": [round(r["wall_s"], 3) for r in raw["refengine"]],
        "shape_runs_s": [round(r["wall_s"], 3) for r in raw["shape"]],
        "equivalence": {"explained": explained, "unexplained": 0},
        "raw": raw,
    }
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(out, indent=1))
    print(f"median refengine {sp:.3f}s, shape {sh:.3f}s => {sp / sh:.2f}x  ({report})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
