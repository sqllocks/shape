"""Fair timing harness: Spindle DataProfiler vs the 1:1 port.

    flock /tmp/claude-0/bench.lock /tmp/claude-0/venv/bin/python bench.py [--reps 5] [dataset ...]

* one fresh process per timed run (no state can be cached between runs)
* interpreter start-up and imports are excluded for both tools: the worker imports
  everything, then times  read file + full profile  with perf_counter
    Spindle CSV      DataProfiler.from_csv(path)
    Spindle Parquet  DataProfiler().profile(pd.read_parquet(path), stem)   (Spindle has no
                     from_parquet; this is what its CLI / demo code does)
    Spindle MT       pd.read_csv each file + DataProfiler().profile_dataset(tables)
    port             profile_csv / profile_parquet / profile_dataset(paths)
* modes: spindle (as shipped), port-mt (default threading), port-st (PROFILE_THREADS=1,
  pyarrow cpu+io pools = 1, OPENBLAS/OMP threads = 1)
* runs are interleaved (spindle, port-mt, port-st, spindle, ...), page cache warmed once
  per file before the first run, median of --reps reported
* before every run the 1-min load average is checked; if > 1.5 the harness waits
* peak RSS: ru_maxrss of the worker (and, for the port's fork pool, the largest child)
"""
from __future__ import annotations

import json
import os
import platform
import statistics
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = Path(os.environ.get("PROFILE_DATA_DIR", "/tmp/claude-0/profile_data"))
SPINDLE_PY = "/tmp/claude-0/spindle-venv/bin/python"
PORT_PY = "/tmp/claude-0/venv/bin/python"
OUT = Path("/tmp/claude-0/profile_scratch/bench_results.json")
DATASETS = ["d1.csv", "d1.parquet", "d2.csv", "d2.parquet", "d3.csv", "d3.parquet",
            "d4.csv", "d4.parquet", "mt"]
LOAD_MAX = 1.5


# ---------------------------------------------------------------------------
# worker (runs inside the tool's own venv)
# ---------------------------------------------------------------------------

def _rss_kb():
    import resource
    return (resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss)


def worker(tool: str, ds: str):
    path = DATA / ds
    if tool == "spindle":
        sys.path.insert(0, "/home/user/sqllocks/spindle")
        import pandas as pd
        from sqllocks_spindle.inference.profiler import DataProfiler
        import scipy.stats  # noqa: F401  (already imported by the profiler module)

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
    ncols = sum(len(t.columns) for t in prof.tables.values()) if hasattr(prof, "tables") else len(prof.columns)
    print(json.dumps({"seconds": dt, "rss_base_kb": base, "rss_peak_kb": self_kb,
                      "rss_child_peak_kb": child_kb, "ncols": ncols}))


# ---------------------------------------------------------------------------
# driver
# ---------------------------------------------------------------------------

MODES = {
    "spindle": (SPINDLE_PY, {}),
    "port-mt": (PORT_PY, {}),
    "port-st": (PORT_PY, {"PROFILE_THREADS": "1", "OPENBLAS_NUM_THREADS": "1",
                          "OMP_NUM_THREADS": "1"}),
}


def wait_for_quiet(log: list):
    waited = 0
    while True:
        load = os.getloadavg()[0]
        if load <= LOAD_MAX or waited >= 900:
            log.append(load)
            return load
        time.sleep(15)
        waited += 15


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
    for k in ("PROFILE_THREADS", "OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS"):
        env.pop(k, None)
    env.update(extra)
    tool = "spindle" if mode == "spindle" else "port"
    r = subprocess.run([py, str(Path(__file__).resolve()), "--worker", tool, ds],
                       env=env, capture_output=True, text=True, check=True)
    return json.loads(r.stdout.strip().splitlines()[-1])


def main():
    args = sys.argv[1:]
    reps = 5
    if "--reps" in args:
        i = args.index("--reps")
        reps = int(args[i + 1])
        del args[i:i + 2]
    datasets = args or DATASETS
    results = json.loads(OUT.read_text()) if OUT.exists() else {}
    meta = {
        "cores": os.cpu_count(),
        "cpu": next((ln.split(":", 1)[1].strip() for ln in open("/proc/cpuinfo") if ln.startswith("model name")), "?"),
        "platform": platform.platform(),
        "started": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    results["_meta"] = meta
    print(f"cores={meta['cores']} cpu={meta['cpu']}", flush=True)
    for ds in datasets:
        warm(ds)
        runs = {m: [] for m in MODES}
        loads = []
        for rep in range(reps):
            for mode in MODES:
                load = wait_for_quiet(loads)
                r = run_once(mode, ds)
                r["load_before"] = load
                runs[mode].append(r)
                print(f"{ds:11s} rep{rep} {mode:8s} {r['seconds']:8.2f}s load={load:.2f} "
                      f"rss={r['rss_peak_kb'] / 1024:.0f}MB child={r['rss_child_peak_kb'] / 1024:.0f}MB",
                      flush=True)
        results[ds] = {m: {"median_s": statistics.median(x["seconds"] for x in v),
                           "min_s": min(x["seconds"] for x in v),
                           "max_s": max(x["seconds"] for x in v),
                           "peak_rss_mb": max(x["rss_peak_kb"] for x in v) / 1024,
                           "peak_child_rss_mb": max(x["rss_child_peak_kb"] for x in v) / 1024,
                           "base_rss_mb": statistics.median(x["rss_base_kb"] for x in v) / 1024,
                           "runs": [x["seconds"] for x in v],
                           "loads": [x["load_before"] for x in v]}
                       for m, v in runs.items()}
        OUT.write_text(json.dumps(results, indent=1))
    print_table(results)


def print_table(results: dict):
    print("\n| dataset | Spindle s | port MT s | port 1T s | speedup MT | speedup 1T | "
          "peak RSS Spindle / port MT (+child) / port 1T MB |")
    print("|---|---:|---:|---:|---:|---:|---|")
    for ds, r in results.items():
        if ds.startswith("_"):
            continue
        s, m, o = r["spindle"], r["port-mt"], r["port-st"]
        print(f"| {ds} | {s['median_s']:.2f} | {m['median_s']:.2f} | {o['median_s']:.2f} | "
              f"{s['median_s'] / m['median_s']:.1f}x | {s['median_s'] / o['median_s']:.1f}x | "
              f"{s['peak_rss_mb']:.0f} / {m['peak_rss_mb']:.0f} (+{m['peak_child_rss_mb']:.0f}) / "
              f"{o['peak_rss_mb']:.0f} |")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--worker":
        worker(sys.argv[2], sys.argv[3])
    elif len(sys.argv) > 1 and sys.argv[1] == "--table":
        print_table(json.loads(OUT.read_text()))
    else:
        main()
