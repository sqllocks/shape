"""Release and candidate profiling: same Shape worker in separate interpreters.

Fresh processes exclude imports, interleave tools, warm input files, hold the
benchmark lock and report median wall time and RSS. run.py requires authenticated
T-22 equivalence before invoking this benchmark. Both sides call shape.profile.
Default threading is compared with single-thread candidate profiling as well.
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
from typing import Any, cast

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from common import bench_lock, machine_meta, maxrss_kib, wait_for_quiet  # noqa: E402
from paths import (  # noqa: E402
    BASELINE_PY,
    BENCH_OUT_DIR,
    PROFILE_DATA_DIR,
    SHAPE_PY,
)

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


def _rss_kb() -> Any:
    return maxrss_kib(), maxrss_kib(children=True)


def worker(tool: str, ds: str) -> Any:
    path = DATA / ("mt" if ds in ("mt", "mt.parquet") else ds)
    if tool in ("shape", "baseline"):
        import shape
        import shape.api  # noqa: F401  (what shape.profile resolves to on its first call)

        # T-19: imports are excluded for both tools. Shape release's worker imports pandas and its
        # profiler above; shape.profile imports its implementation (pandas included) on first
        # call, so import that module here too. Nothing is run: no warm-up call.
        import shape.profile.reference.profile  # noqa: F401
        from shape.kernel.dispatch import get_kernel

        get_kernel()  # loads the native extension: an import, not work

        if ds in ("mt", "mt.parquet"):

            def run() -> Any:
                return shape.profile(
                    {
                        p.stem: str(p)
                        for p in sorted(path.glob("*.parquet" if ds == "mt.parquet" else "*.csv"))
                    }
                )
        else:

            def run() -> Any:
                return shape.profile(str(path))

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
    "baseline": (str(BASELINE_PY), {}),
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
IMPL = "shape"


def warm(ds: str) -> Any:
    p = DATA / ("mt" if ds in ("mt", "mt.parquet") else ds)
    files = sorted(p.glob("*.parquet" if ds == "mt.parquet" else "*.csv")) if p.is_dir() else [p]
    for f in files:
        with open(f, "rb") as fh:
            while fh.read(1 << 24):
                pass


def run_once(mode: str, ds: str) -> dict[str, Any]:
    py, extra = MODES[mode]
    env = dict(os.environ)
    for k in ("PROFILE_THREADS", "SHAPE_THREADS", "OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS"):
        env.pop(k, None)
    env.update(extra)
    if mode == "baseline":
        env.pop("PYTHONPATH", None)
        env.pop("PYTHONHOME", None)
    tool = "baseline" if mode == "baseline" else IMPL
    r = subprocess.run(
        [py, str(Path(__file__).resolve()), "--worker", tool, ds],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return cast(dict[str, Any], json.loads(r.stdout.strip().splitlines()[-1]))


def main(argv: list[str] | None = None) -> None:
    global IMPL, OUT, MODES
    ap = argparse.ArgumentParser(
        description="profiling benchmark: Shape release vs an implementation"
    )
    ap.add_argument("--impl", choices=["shape"], required=True)
    ap.add_argument("--reps", type=int, default=5)
    ap.add_argument("--out", default=None, help=f"results JSON (default: {OUT})")
    ap.add_argument("datasets", nargs="*", help="default: all")
    ap.add_argument("--baseline-only", action="store_true")
    args = ap.parse_args(argv)
    if args.baseline_only:
        MODES = {"baseline": MODES["baseline"]}
    IMPL = args.impl
    if args.out:
        OUT = Path(args.out)
    datasets = args.datasets or DATASETS
    missing = [d for d in datasets if not (DATA / ("mt" if d == "mt.parquet" else d)).exists()]
    if missing:
        sys.exit(f"missing datasets under {DATA}: {missing}; run datasets.py")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    results: dict[str, Any] = {}
    meta = {**machine_meta(), "impl": IMPL, "reps": args.reps}
    results["_meta"] = meta
    print(f"cores={meta['cores']} cpu={meta['cpu']}", flush=True)
    with bench_lock():
        for ds in datasets:
            warm(ds)
            runs: dict[str, list[Any]] = {m: [] for m in MODES}
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


def print_table(results: dict[str, Any]) -> Any:
    for ds, rec in results.items():
        if ds.startswith("_"):
            continue
        print(
            ds + ": " + ", ".join(f"{tool} {data['median_s']:.3f}s" for tool, data in rec.items())
        )


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--worker":
        worker(sys.argv[2], sys.argv[3])
    elif len(sys.argv) > 1 and sys.argv[1] == "--table":
        print_table(json.loads(Path(sys.argv[2] if len(sys.argv) > 2 else OUT).read_text()))
    else:
        main()
