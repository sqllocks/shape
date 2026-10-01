"""Fair timing harness: Spindle DataProfiler vs an implementation (reference_port or shape).

    source scripts/env.sh && "$SHAPE_VENV/bin/python" benchmarks/vs_spindle/profile_1to1/bench.py \
        --impl reference_port|shape [--reps 5] [dataset ...]

The whole run holds the exclusive benchmark lock ($BENCH_OUT_DIR/bench.lock).

* one fresh process per timed run (no state can be cached between runs)
* interpreter start-up and imports are excluded for both tools: the worker imports
  everything, then times  read file + full profile  with perf_counter
    Spindle CSV      DataProfiler.from_csv(path)
    Spindle Parquet  DataProfiler().profile(pd.read_parquet(path), stem)   (Spindle has no
                     from_parquet; this is what its CLI / demo code does)
    Spindle MT       pd.read_csv each file + DataProfiler().profile_dataset(tables)
    reference_port   profile_csv / profile_parquet / profile_dataset(paths)
    shape            shape.profile(path) / shape.profile({table: path, ...})
* modes: spindle (as shipped), impl_mt (default threading), impl_st (PROFILE_THREADS=1,
  pyarrow cpu+io pools = 1, OPENBLAS/OMP threads = 1)
* runs are interleaved (spindle, impl_mt, impl_st, spindle, ...), page cache warmed once
  per file before the first run, median of --reps reported
* before every run the 1-min load average is checked; if > 1.5 the harness waits
* peak RSS: ru_maxrss of the worker (and, for the port's fork pool, the largest child)

Equivalence first: run verify.py --impl <impl> and require exit 0 before trusting timings
(run.py does this).
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
from common import bench_lock, machine_meta, wait_for_quiet  # noqa: E402
from paths import BENCH_OUT_DIR, PROFILE_DATA_DIR, SHAPE_PY, SPINDLE_PY, SPINDLE_ROOT  # noqa: E402

DATA = PROFILE_DATA_DIR
OUT = BENCH_OUT_DIR / "profile" / "bench_results.json"
DATASETS = [
    "d1.csv",
    "d1.parquet",
    "d2.csv",
    "d2.parquet",
    "d3.csv",
    "d3.parquet",
    "d4.csv",
    "d4.parquet",
    "mt",
]


# ---------------------------------------------------------------------------
# worker (runs inside the tool's own venv)
# ---------------------------------------------------------------------------


def _rss_kb():
    import resource

    return (
        resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,
    )


def worker(tool: str, ds: str):
    path = DATA / ds
    if tool == "spindle":
        sys.path.insert(0, str(SPINDLE_ROOT))
        import pandas as pd
        import scipy.stats  # noqa: F401  (already imported by the profiler module)
        from sqllocks_spindle.inference.profiler import DataProfiler

        if ds == "mt":

            def run():
                tables = {p.stem: pd.read_csv(p) for p in sorted(path.glob("*.csv"))}
                return DataProfiler().profile_dataset(tables)
        elif ds.endswith(".parquet"):

            def run():
                return DataProfiler().profile(pd.read_parquet(path), table_name=path.stem)
        else:

            def run():
                return DataProfiler.from_csv(path)
    elif tool == "shape":
        import shape
        import shape.api  # noqa: F401  (what shape.profile resolves to on its first call)

        # T-19: imports are excluded for both tools. Spindle's worker imports pandas and its
        # profiler above; shape.profile imports its implementation (pandas included) on first
        # call, so import that module here too. Nothing is run: no warm-up call.
        import shape.profile.reference.profile  # noqa: F401
        from shape.kernel.dispatch import get_kernel

        get_kernel()  # loads the native extension: an import, not work

        if ds == "mt":

            def run():
                return shape.profile({p.stem: str(p) for p in sorted(path.glob("*.csv"))})
        else:

            def run():
                return shape.profile(str(path))
    else:
        sys.path.insert(0, str(HERE))
        import port

        if ds == "mt":

            def run():
                return port.profile_dataset({p.stem: str(p) for p in sorted(path.glob("*.csv"))})
        elif ds.endswith(".parquet"):

            def run():
                return port.profile_parquet(path)
        else:

            def run():
                return port.profile_csv(path)

    base = _rss_kb()[0]
    t0 = time.perf_counter()
    prof = run()
    dt = time.perf_counter() - t0
    self_kb, child_kb = _rss_kb()
    ncols = (
        sum(len(t["columns"] if isinstance(t, dict) else t.columns) for t in prof.tables.values())
        if hasattr(prof, "tables")
        else len(prof.columns)
    )
    print(
        json.dumps(
            {
                "seconds": dt,
                "rss_base_kb": base,
                "rss_peak_kb": self_kb,
                "rss_child_peak_kb": child_kb,
                "ncols": ncols,
            }
        )
    )


# ---------------------------------------------------------------------------
# driver
# ---------------------------------------------------------------------------

MODES = {
    "spindle": (str(SPINDLE_PY), {}),
    "impl_mt": (str(SHAPE_PY), {}),
    "impl_st": (
        str(SHAPE_PY),
        {
            "PROFILE_THREADS": "1",
            "SHAPE_THREADS": "1",
            "OPENBLAS_NUM_THREADS": "1",
            "OMP_NUM_THREADS": "1",
        },
    ),
}
IMPL = "reference_port"


def warm(ds: str):
    p = DATA / ds
    files = sorted(p.glob("*.csv")) if p.is_dir() else [p]
    for f in files:
        with open(f, "rb") as fh:
            while fh.read(1 << 24):
                pass


def run_once(mode: str, ds: str) -> dict:
    py, extra = MODES[mode]
    env = dict(os.environ)
    for k in ("PROFILE_THREADS", "SHAPE_THREADS", "OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS"):
        env.pop(k, None)
    env.update(extra)
    tool = "spindle" if mode == "spindle" else IMPL
    r = subprocess.run(
        [py, str(Path(__file__).resolve()), "--worker", tool, ds],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(r.stdout.strip().splitlines()[-1])


def main(argv: list[str] | None = None) -> None:
    global IMPL, OUT
    ap = argparse.ArgumentParser(description="profiling benchmark: Spindle vs an implementation")
    ap.add_argument("--impl", choices=["reference_port", "shape"], required=True)
    ap.add_argument("--reps", type=int, default=5)
    ap.add_argument("--out", default=None, help=f"results JSON (default: {OUT})")
    ap.add_argument("datasets", nargs="*", help="default: all")
    args = ap.parse_args(argv)
    IMPL = args.impl
    if args.out:
        OUT = Path(args.out)
    datasets = args.datasets or DATASETS
    missing = [d for d in datasets if not (DATA / d).exists()]
    if missing:
        sys.exit(f"missing datasets under {DATA}: {missing}; run datasets.py")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    results: dict = {}
    meta = {**machine_meta(), "impl": IMPL, "reps": args.reps}
    results["_meta"] = meta
    print(f"cores={meta['cores']} cpu={meta['cpu']}", flush=True)
    with bench_lock():
        for ds in datasets:
            warm(ds)
            runs: dict[str, list] = {m: [] for m in MODES}
            for rep in range(args.reps):
                for mode in MODES:
                    load = wait_for_quiet()
                    r = run_once(mode, ds)
                    r["load_before"] = load
                    runs[mode].append(r)
                    print(
                        f"{ds:11s} rep{rep} {mode:8s} {r['seconds']:8.2f}s load={load:.2f} "
                        f"rss={r['rss_peak_kb'] / 1024:.0f}MB "
                        f"child={r['rss_child_peak_kb'] / 1024:.0f}MB",
                        flush=True,
                    )
            results[ds] = {
                m: {
                    "median_s": statistics.median(x["seconds"] for x in v),
                    "min_s": min(x["seconds"] for x in v),
                    "max_s": max(x["seconds"] for x in v),
                    "peak_rss_mb": max(x["rss_peak_kb"] for x in v) / 1024,
                    "peak_child_rss_mb": max(x["rss_child_peak_kb"] for x in v) / 1024,
                    "base_rss_mb": statistics.median(x["rss_base_kb"] for x in v) / 1024,
                    "runs": [x["seconds"] for x in v],
                    "loads": [x["load_before"] for x in v],
                }
                for m, v in runs.items()
            }
            OUT.write_text(json.dumps(results, indent=1))
    print_table(results)


def print_table(results: dict):
    print(
        "\n| dataset | Spindle s | port MT s | port 1T s | speedup MT | speedup 1T | "
        "peak RSS Spindle / port MT (+child) / port 1T MB |"
    )
    print("|---|---:|---:|---:|---:|---:|---|")
    for ds, r in results.items():
        if ds.startswith("_"):
            continue
        s, m, o = r["spindle"], r["impl_mt"], r["impl_st"]
        print(
            f"| {ds} | {s['median_s']:.2f} | {m['median_s']:.2f} | {o['median_s']:.2f} | "
            f"{s['median_s'] / m['median_s']:.1f}x | {s['median_s'] / o['median_s']:.1f}x | "
            f"{s['peak_rss_mb']:.0f} / {m['peak_rss_mb']:.0f} (+{m['peak_child_rss_mb']:.0f}) / "
            f"{o['peak_rss_mb']:.0f} |"
        )


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--worker":
        worker(sys.argv[2], sys.argv[3])
    elif len(sys.argv) > 1 and sys.argv[1] == "--table":
        print_table(json.loads(Path(sys.argv[2] if len(sys.argv) > 2 else OUT).read_text()))
    else:
        main()
