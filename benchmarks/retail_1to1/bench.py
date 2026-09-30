"""Fair benchmark harness: Spindle retail vs the vectorized port.

    flock /tmp/claude-0/bench.lock /tmp/claude-0/spindle-venv/bin/python bench.py --scales medium,large

Every run is a fresh subprocess.  Timed region, for each tool:
    construct domain / load config + reference data + pools
  + generate all 9 tables (incl. compute phase + business-rule fixing)
  + write each table to Parquet
      Spindle: PandasWriter.to_parquet (the `spindle generate --format parquet`
               CLI path: sequential df.to_parquet(path, index=False), snappy)
      port:    sequential pyarrow.parquet.write_table(table, path, compression="snappy")
Library imports happen before the timed region for both (reported separately).
Output directories are deleted after each run (outside the timed region).
Tools are interleaved run-by-run; median of N runs is reported.
Before every run the harness waits until the 1-min load average is < 1.5.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import resource
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPINDLE_ROOT = "/home/user/sqllocks/spindle"
SPINDLE_PY = "/tmp/claude-0/spindle-venv/bin/python"
SHAPE_PY = "/tmp/claude-0/venv/bin/python"
OUT_ROOT = Path("/tmp/claude-0/retail_scratch/bench_out")


def _peak_rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def _cpu() -> float:
    r = resource.getrusage(resource.RUSAGE_SELF)
    return r.ru_utime + r.ru_stime


def _ru() -> dict:
    r = resource.getrusage(resource.RUSAGE_SELF)
    return {"user": r.ru_utime, "sys": r.ru_stime, "minflt": r.ru_minflt, "majflt": r.ru_majflt}


def _ru_delta(a: dict, b: dict) -> dict:
    return {"user_s": b["user"] - a["user"], "sys_s": b["sys"] - a["sys"],
            "minor_faults": b["minflt"] - a["minflt"], "major_faults": b["majflt"] - a["majflt"]}


# ─────────────────────────────── child runners ───────────────────────────────

def child_spindle(scale: str, seed: int, out: Path) -> dict:
    t_imp = time.perf_counter()
    sys.path.insert(0, SPINDLE_ROOT)
    import numpy  # noqa: F401
    import pandas  # noqa: F401
    import pyarrow  # noqa: F401
    from sqllocks_spindle import Spindle
    from sqllocks_spindle.domains.retail import RetailDomain
    from sqllocks_spindle.output.pandas_writer import PandasWriter
    import_s = time.perf_counter() - t_imp
    rss_after_import = _peak_rss_mb()

    marks: list[tuple[str, float]] = []
    ru0 = _ru()
    cpu0 = _cpu()
    t0 = time.perf_counter()
    domain = RetailDomain()
    sp = Spindle()
    t_setup = time.perf_counter()
    res = sp.generate(domain=domain, scale=scale, seed=seed,
                      on_progress=lambda name, done, total: marks.append((name, time.perf_counter())))
    t1 = time.perf_counter()
    PandasWriter().to_parquet(res.tables, out)
    t2 = time.perf_counter()
    cpu = _cpu() - cpu0
    ru = _ru_delta(ru0, _ru())

    per_table, prev = {}, t_setup
    for name, t in marks:
        per_table[name] = t - prev
        prev = t
    per_table["_compute_phase+business_rules"] = t1 - prev
    per_table["_construct_domain+Spindle()"] = t_setup - t0
    rows = {k: len(v) for k, v in res.tables.items()}
    return {"import_s": import_s, "rss_after_import_mb": rss_after_import, "gen_s": t1 - t0,
            "write_s": t2 - t1, "total_s": t2 - t0, "cpu_s": cpu, "rows": rows,
            "per_table_s": per_table, **ru}


def child_port(scale: str, seed: int, out: Path) -> dict:
    t_imp = time.perf_counter()
    import numpy  # noqa: F401
    import pyarrow  # noqa: F401
    import pyarrow.compute  # noqa: F401
    import pyarrow.parquet  # noqa: F401
    sys.path.insert(0, str(HERE))
    import port
    # pyarrow lazily imports pandas (if installed) on the first pa.array(list);
    # Spindle imports pandas before its timed region, so do the same here.
    pyarrow.array(["warm"])
    import_s = time.perf_counter() - t_imp
    rss_after_import = _peak_rss_mb()

    ru0 = _ru()
    cpu0 = _cpu()
    t0 = time.perf_counter()
    tables, eng = port.generate(scale, seed, SPINDLE_ROOT, return_engine=True)
    t1 = time.perf_counter()
    port.write_parquet(tables, out)
    t2 = time.perf_counter()
    cpu = _cpu() - cpu0
    ru = _ru_delta(ru0, _ru())
    rows = {k: v.num_rows for k, v in tables.items()}
    return {"import_s": import_s, "rss_after_import_mb": rss_after_import, "gen_s": t1 - t0,
            "write_s": t2 - t1, "total_s": t2 - t0, "cpu_s": cpu, "rows": rows,
            "per_table_s": dict(eng.timings), "per_column_s": dict(eng.col_timings),
            "numpy": numpy.__version__, "pyarrow": pyarrow.__version__, **ru}


def child_main(tool: str, scale: str, seed: int, out: str):
    out_p = Path(out)
    fn = child_spindle if tool == "spindle" else child_port
    r = fn(scale, seed, out_p)
    r["parquet_bytes"] = sum(p.stat().st_size for p in out_p.glob("*.parquet"))
    r["peak_rss_mb"] = _peak_rss_mb()
    print("BENCH_JSON " + json.dumps(r))


# ─────────────────────────────── orchestration ───────────────────────────────

TOOLS = {
    "spindle": (SPINDLE_PY, "spindle"),
    "port": (SPINDLE_PY, "port"),                  # same interpreter + numpy/pyarrow as Spindle
    "port_shape_venv": (SHAPE_PY, "port"),         # the shape venv (pyarrow 23, no pandas)
}


def loadavg() -> float:
    return float(Path("/proc/loadavg").read_text().split()[0])


def wait_for_quiet(limit: float = 1.5, max_wait_s: float = 900) -> float:
    t = time.time()
    la = loadavg()
    while la >= limit and time.time() - t < max_wait_s:
        time.sleep(10)
        la = loadavg()
    return la


def _busy_cpu_s() -> float:
    """Machine-wide busy CPU seconds (all cores) from /proc/stat."""
    f = Path("/proc/stat").read_text().splitlines()[0].split()[1:]
    user, nice, system, idle, iowait, irq, softirq, steal = (int(x) for x in f[:8])
    return (user + nice + system + irq + softirq + steal) / os.sysconf("SC_CLK_TCK")


def run_once(tool: str, scale: str, seed: int, idx: int) -> dict:
    py, kind = TOOLS[tool]
    out = OUT_ROOT / f"{tool}_{scale}_{idx}"
    shutil.rmtree(out, ignore_errors=True)
    la = wait_for_quiet()
    ch0 = resource.getrusage(resource.RUSAGE_CHILDREN)
    busy0, w0 = _busy_cpu_s(), time.perf_counter()
    p = subprocess.run([py, str(Path(__file__).resolve()), "--child", kind, "--scale", scale,
                        "--seed", str(seed), "--out", str(out)],
                       capture_output=True, text=True, timeout=3600)
    busy1, w1 = _busy_cpu_s(), time.perf_counter()
    ch1 = resource.getrusage(resource.RUSAGE_CHILDREN)
    child_cpu = (ch1.ru_utime - ch0.ru_utime) + (ch1.ru_stime - ch0.ru_stime)
    shutil.rmtree(out, ignore_errors=True)
    line = next((l for l in p.stdout.splitlines() if l.startswith("BENCH_JSON ")), None)
    if p.returncode != 0 or line is None:
        raise RuntimeError(f"{tool} failed:\n{p.stdout[-2000:]}\n{p.stderr[-4000:]}")
    r = json.loads(line[len("BENCH_JSON "):])
    r["loadavg_before"] = la
    # CPU used by *other* processes on the machine while this run's process was alive
    r["other_procs_cpu_s"] = max(0.0, (busy1 - busy0) - child_cpu)
    r["other_procs_avg_cores"] = r["other_procs_cpu_s"] / max(w1 - w0, 1e-9)
    return r


def summarize(runs: list[dict]) -> dict:
    med = lambda k: statistics.median(r[k] for r in runs)  # noqa: E731
    rows = sum(runs[0]["rows"].values())
    s = {k: med(k) for k in ("total_s", "gen_s", "write_s", "cpu_s", "user_s", "sys_s", "minor_faults",
                              "import_s", "peak_rss_mb", "rss_after_import_mb", "parquet_bytes")}
    s["rows"] = rows
    s["rows_per_s_total"] = rows / s["total_s"]
    s["rows_per_s_generate"] = rows / s["gen_s"]
    s["runs_total_s"] = [round(r["total_s"], 3) for r in runs]
    s["loadavg_before"] = [r["loadavg_before"] for r in runs]
    s["other_procs_avg_cores"] = [round(r["other_procs_avg_cores"], 3) for r in runs]
    keys = runs[0]["per_table_s"].keys()
    s["per_table_s_median"] = {k: statistics.median(r["per_table_s"].get(k, 0.0) for r in runs) for k in keys}
    s["row_counts"] = runs[0]["rows"]
    for k in ("numpy", "pyarrow"):
        if k in runs[0]:
            s[k] = runs[0][k]
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--child")
    ap.add_argument("--scale", default="medium")
    ap.add_argument("--scales", default="medium")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--tools", default="spindle,port,port_shape_venv")
    ap.add_argument("--report", default=str(HERE / "bench_results.json"))
    a = ap.parse_args()
    if a.child:
        return child_main(a.child, a.scale, a.seed, a.out)

    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    tools = a.tools.split(",")
    env = {"cpu_count": os.cpu_count(), "sched_affinity": len(os.sched_getaffinity(0)),
           "platform": platform.platform(), "python": platform.python_version(),
           "loadavg_start": Path("/proc/loadavg").read_text().strip(),
           "started": time.strftime("%Y-%m-%d %H:%M:%S")}
    results: dict = {"env": env, "seed": a.seed, "runs": a.runs, "scales": {}}
    if Path(a.report).exists():
        prev = json.loads(Path(a.report).read_text())
        results["scales"] = prev.get("scales", {})
    for scale in a.scales.split(","):
        raw: dict[str, list] = {t: [] for t in tools}
        for i in range(a.runs):
            for t in tools:  # interleave tools run-by-run
                for attempt in range(4):  # retry runs disturbed by other processes
                    r = run_once(t, scale, a.seed, i)
                    r["attempt"] = attempt + 1
                    if r["other_procs_avg_cores"] <= 0.75:
                        break
                    print(f"[{scale}] {t} run {i + 1} disturbed ({r['other_procs_avg_cores']:.2f} other cores), "
                          f"retrying", flush=True)
                    time.sleep(30)
                raw[t].append(r)
                print(f"[{scale}] {t:16s} run {i + 1}: total {r['total_s']:.2f}s  gen {r['gen_s']:.2f}s  "
                      f"write {r['write_s']:.2f}s  rss {r['peak_rss_mb']:.0f}MB  load {r['loadavg_before']:.2f}  "
                      f"other-procs {r['other_procs_avg_cores']:.2f} cores",
                      flush=True)
        results["scales"][scale] = {"summary": {t: summarize(raw[t]) for t in tools}, "raw": raw}
        Path(a.report).write_text(json.dumps(results, indent=1))
    results["env"]["loadavg_end"] = Path("/proc/loadavg").read_text().strip()
    Path(a.report).write_text(json.dumps(results, indent=1))
    print_table(results)


def print_table(results: dict):
    for scale, d in results["scales"].items():
        S = d["summary"]
        print(f"\n== {scale}: median of {results['runs']} fresh-process runs, seed {results['seed']}, "
              f"{results['env']['cpu_count']} cores")
        print(f"{'tool':18s} {'rows':>10s} {'total s':>8s} {'gen s':>7s} {'write s':>8s} {'rows/s total':>13s} "
              f"{'rows/s gen':>12s} {'user s':>7s} {'sys s':>6s} {'minflt':>9s} {'peak RSS MB':>12s}")
        for t, s in S.items():
            print(f"{t:18s} {s['rows']:>10,} {s['total_s']:>8.2f} {s['gen_s']:>7.2f} {s['write_s']:>8.2f} "
                  f"{s['rows_per_s_total']:>13,.0f} {s['rows_per_s_generate']:>12,.0f} {s['user_s']:>7.2f} "
                  f"{s['sys_s']:>6.2f} {s['minor_faults']:>9,} "
                  f"{s['peak_rss_mb']:>12.0f}")
        if "spindle" in S and "port" in S:
            print(f"speedup (port vs spindle, same venv): total {S['spindle']['total_s'] / S['port']['total_s']:.2f}x, "
                  f"generate {S['spindle']['gen_s'] / S['port']['gen_s']:.2f}x, "
                  f"write {S['spindle']['write_s'] / S['port']['write_s']:.2f}x")


if __name__ == "__main__":
    main()
