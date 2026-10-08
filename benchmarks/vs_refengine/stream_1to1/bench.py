"""STREAM-EMIT timing: the baseline's stream command against ``shape stream``, one table to a file.

    source scripts/env.sh
    "$SHAPE_VENV/bin/python" benchmarks/vs_refengine/stream_1to1/bench.py \\
        --scale medium --runs 5

Equivalence first (plan 6.4): ``run.py`` runs ``verify.py`` before this and again, with
``--no-generate``, on the output this writes. Every run is a fresh process of a worker in the
tool's own venv (``baseline_worker.py``, ``shape_worker.py``); the timed region is generate +
convert to events + emit to the file, with library imports done before it for both tools (T-19).
Tools alternate run by run, after ``--warmup`` discarded runs; the median of ``--runs`` is the
figure. The output files written are the ones the verifier reads (``stream_common.out_file``:
baseline seed 42, Shape seed 1042), and each run replaces the last, so nothing is cached between
timed runs. A second record times the two command lines end to end (process start-up included),
the secondary measure of T-19. The whole run holds the exclusive benchmark lock and each run waits
for a 1-minute load average of at most 1.5.
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
import stream_common as sc  # noqa: E402
from common import bench_lock, machine_meta, wait_for_quiet  # noqa: E402
from paths import BENCH_OUT_DIR, REFENGINE_PY, REFENGINE_VENV, SHAPE_PY, SHAPE_VENV  # noqa: E402

SUMMARY_KEYS = ("total_s", "emit_s", "cpu_s", "import_s", "peak_rss_mb", "bytes")


def _worker(tool: str, scale: str, max_events: int | None) -> list[str]:
    py, script, seed = (
        (REFENGINE_PY, "baseline_worker.py", sc.REF_SEED)
        if tool == "refengine"
        else (SHAPE_PY, "shape_worker.py", sc.SHAPE_SEED)
    )
    out = sc.out_file(tool, scale, seed, max_events)
    cmd = [str(py), str(HERE / script), "--scale", scale, "--seed", str(seed), "-o", str(out)]
    if max_events is not None:
        cmd += ["--max-events", str(max_events)]
    return cmd


def run_once(tool: str, scale: str, max_events: int | None) -> dict:
    load = wait_for_quiet()
    cmd = _worker(tool, scale, max_events)
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} failed:\n{r.stderr[-2000:]}")
    line = next(ln for ln in reversed(r.stdout.splitlines()) if ln.startswith("STREAM_JSON "))
    rec = json.loads(line[len("STREAM_JSON ") :])
    rec["loadavg_before"] = load
    return rec


def cli_command(tool: str, scale: str, max_events: int | None) -> list[str]:
    """The workload's command line (plan 3.4), with the section 1 variables expanded."""
    if tool == "refengine":
        exe, seed = REFENGINE_VENV / "bin" / _refpkg.CONSOLE, sc.REF_SEED
    else:
        exe, seed = SHAPE_VENV / "bin" / "shape", sc.SHAPE_SEED
    out = sc.out_file(tool, scale, seed, max_events)
    cmd = [str(exe), "stream", sc.DOMAIN, "--table", sc.TABLE, "--scale", scale, "--no-realtime"]
    cmd += ["--sink", "file", "-o", str(out), "--seed", str(seed)]
    if max_events is not None:
        cmd += ["--max-events", str(max_events)]
    return cmd


def run_cli(tool: str, scale: str, max_events: int | None) -> float:
    wait_for_quiet()
    cmd = cli_command(tool, scale, max_events)
    out = Path(cmd[cmd.index("-o") + 1])
    out.unlink(missing_ok=True)
    Path(f"{out}.checkpoint").unlink(missing_ok=True)
    t0 = time.perf_counter()
    r = subprocess.run(cmd, capture_output=True, text=True)
    dt = time.perf_counter() - t0
    if r.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} failed:\n{r.stderr[-2000:]}")
    return dt


def summarize(runs: list[dict]) -> dict:
    s = {k: statistics.median(r[k] for r in runs) for k in SUMMARY_KEYS}
    s["events"] = runs[0]["events"]
    s["events_per_s"] = s["events"] / s["total_s"]
    s["runs_total_s"] = [round(r["total_s"], 3) for r in runs]
    s["loadavg_before"] = [r["loadavg_before"] for r in runs]
    return s


def bench(scale: str, runs: int, warmup: int, cli_runs: int, report: Path) -> dict:
    tools = ["refengine", "shape"]
    results: dict = {
        "meta": {
            **machine_meta(),
            "workload": f"stream:{sc.DOMAIN}:{sc.TABLE}:{scale}",
            "runs": runs,
            "warmup": warmup,
            "ref_seed": sc.REF_SEED,
            "impl_seed": sc.SHAPE_SEED,
            "threads": "default (Shape: all cores; SHAPE_THREADS=1 is reported alongside)",
            "loadavg_start": os.getloadavg()[0] if hasattr(os, "getloadavg") else 0.0,
        }
    }
    with bench_lock():
        for i in range(warmup):
            for t in tools:
                r = run_once(t, scale, None)
                print(f"[{scale}] {t:8s} warm-up {i + 1}: total {r['total_s']:.2f}s", flush=True)
        raw: dict[str, list] = {t: [] for t in tools}
        for i in range(runs):
            for t in tools:
                r = run_once(t, scale, None)
                raw[t].append(r)
                print(
                    f"[{scale}] {t:8s} run {i + 1}: total {r['total_s']:.2f}s  "
                    f"emit {r['emit_s']:.2f}s  rss {r['peak_rss_mb']:.0f}MB  "
                    f"load {r['loadavg_before']:.2f}",
                    flush=True,
                )
        results["summary"] = {t: summarize(raw[t]) for t in tools}
        results["raw"] = raw
        # secondary: the command lines, process start-up included
        cli: dict[str, list[float]] = {t: [] for t in tools}
        for i in range(cli_runs):
            for t in tools:
                cli[t].append(run_cli(t, scale, None))
                print(f"[{scale}] {t:8s} cli {i + 1}: {cli[t][-1]:.2f}s", flush=True)
        results["cli_end_to_end_s"] = {
            t: {"runs": [round(x, 3) for x in v], "median": statistics.median(v)}
            for t, v in cli.items()
            if v
        }
        # the timed output is what verify.py --no-generate checks next
    sp, sh = results["summary"]["refengine"], results["summary"]["shape"]
    results["speedup"] = sp["total_s"] / sh["total_s"]
    if cli_runs:
        c = results["cli_end_to_end_s"]
        results["cli_speedup"] = c["refengine"]["median"] / c["shape"]["median"]
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(results, indent=1))
    print_table(results)
    return results


def print_table(results: dict) -> None:
    m = results["meta"]
    print(
        f"\n== {m['workload']}: median of {m['runs']} fresh-process runs ({m['warmup']} warm-up), "
        f"{m['cores']} cores, {m['cpu']}"
    )
    head = f"{'tool':8s} {'events':>9s} {'total s':>8s} {'emit s':>7s} {'events/s':>10s}"
    print(head + f" {'RSS MB':>7s}")
    for t, s in results["summary"].items():
        print(
            f"{t:8s} {s['events']:>9,} {s['total_s']:>8.2f} {s['emit_s']:>7.2f} "
            f"{s['events_per_s']:>10,.0f} {s['peak_rss_mb']:>7.0f}"
        )
    print(f"speedup (shape vs baseline, in-process): {results['speedup']:.2f}x")
    if "cli_speedup" in results:
        print(f"speedup (command lines, start-up included): {results['cli_speedup']:.2f}x")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--scale", default=sc.SCALE)
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--warmup", type=int, default=1)
    ap.add_argument("--cli-runs", type=int, default=3, help="end-to-end command-line runs per tool")
    ap.add_argument("--report", default=None)
    a = ap.parse_args(argv)
    report = Path(a.report) if a.report else BENCH_OUT_DIR / "bench" / f"stream_{a.scale}.json"
    bench(a.scale, a.runs, a.warmup, a.cli_runs, report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
