"""Fair benchmark harness: generate + write Parquet, Shape release vs an implementation.

    source scripts/env.sh
    "$SHAPE_VENV/bin/python" benchmarks/vs_refengine/domain_1to1/bench.py \\
        --impl shape --domain retail --scales medium,large --runs 3

Every run is a fresh subprocess of ``generate.py`` in the tool's own venv (Shape release's, or
Shape's), so the timed region is the same code path that the verifier's inputs come from:

    construct domain / load config + reference data + pools
  + generate all tables (incl. compute phase + business-rule fixing)
  + write each table to Parquet
      Both tools: the same Shape Engine and sequential PyArrow Parquet writer.

Library imports happen before the timed region for both (reported separately). The timed
runs write the layout directories ``$BENCH_OUT_DIR/<impl>/<domain>/<scale>/seed42`` (Shape release)
and ``seed1042`` (impl); each run replaces the previous directory, so no cache survives
between runs, and those same directories are then verified (run.py runs verify.py on them).
Tools are interleaved run-by-run; the median of --runs runs is reported. The whole run holds
the exclusive benchmark lock, and each run waits until the 1-minute load average is <= 1.5.

Equivalence first: run verify.py --impl <impl> and require exit 0 before trusting timings.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import generate  # noqa: E402
from common import bench_lock, machine_meta, wait_for_quiet  # noqa: E402
from paths import BASELINE_PY, BENCH_OUT_DIR, SHAPE_PY  # noqa: E402

BASELINE_ONLY = False
REF_SEED = 42
IMPL_SEED = 1042
SUMMARY_KEYS = (
    "total_s",
    "gen_s",
    "write_s",
    "cpu_s",
    "user_s",
    "sys_s",
    "minor_faults",
    "import_s",
    "peak_rss_mb",
    "parquet_bytes",
)


def other_procs_cores(before: float, after: float) -> float:
    return max(0.0, after - before)


def run_once(tool: str, impl: str, domain: str, scale: str) -> dict[str, Any]:
    """One timed run in a fresh process. ``tool`` is 'baseline' or the impl name."""
    py = BASELINE_PY if tool == "baseline" else SHAPE_PY
    seed = REF_SEED if tool == "baseline" else IMPL_SEED
    load = wait_for_quiet()
    cmd = [
        str(py),
        str(HERE / "generate.py"),
        "--impl",
        "shape",
        "--domain",
        domain,
        "--scale",
        scale,
        "--seed",
        str(seed),
    ]
    env = dict(os.environ)
    if tool == "baseline":
        env.pop("PYTHONPATH", None)
        env.pop("PYTHONHOME", None)
    r = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if r.returncode:
        tail = "\n".join((r.stdout + "\n" + r.stderr).splitlines()[-200:])
        print(
            f"generate:{domain}:{scale}: command {' '.join(cmd)}; exit {r.returncode}\n{tail}",
            file=sys.stderr,
        )
    if r.returncode == 2:
        raise generate.Unsupported(r.stderr.strip())
    if r.returncode != 0:
        raise RuntimeError(f"generation process failed (exit {r.returncode})")
    line = next(ln for ln in reversed(r.stdout.splitlines()) if ln.startswith("GEN_JSON "))
    rec = json.loads(line[len("GEN_JSON ") :])
    rec["loadavg_before"] = load
    return cast(dict[str, Any], rec)


def summarize(runs: list[dict[str, Any]]) -> dict[str, Any]:
    def med(k: str) -> float:
        return float(statistics.median(r[k] for r in runs))

    rows = sum(runs[0]["rows"].values())
    s: dict[str, Any] = {k: med(k) for k in SUMMARY_KEYS}
    s["rows"] = rows
    s["rows_per_s_total"] = rows / s["total_s"]
    s["rows_per_s_generate"] = rows / s["gen_s"]
    s["runs_total_s"] = [round(r["total_s"], 3) for r in runs]
    s["loadavg_before"] = [r["loadavg_before"] for r in runs]
    keys = runs[0]["per_table_s"].keys()
    s["per_table_s_median"] = {
        k: statistics.median(r["per_table_s"].get(k, 0.0) for r in runs) for k in keys
    }
    s["row_counts"] = runs[0]["rows"]
    for k in ("numpy", "pyarrow"):
        if k in runs[0]:
            s[k] = runs[0][k]
    return s


def bench(
    impl: str, domain: str, scales: list[str], runs: int, report: Path, warmup: int = 0
) -> dict[str, Any]:
    results: dict[str, Any] = {
        "meta": {
            **machine_meta(),
            "impl": impl,
            "domain": domain,
            "runs": runs,
            "warmup": warmup,
            "ref_seed": REF_SEED,
            "impl_seed": IMPL_SEED,
            "loadavg_start": os.getloadavg()[0] if hasattr(os, "getloadavg") else 0.0,
        },
        "scales": {},
    }
    tools = ["baseline"] if BASELINE_ONLY else ["baseline", impl]
    with bench_lock():
        for scale in scales:
            raw: dict[str, list[Any]] = {t: [] for t in tools}
            for i in range(warmup):  # T-19: discarded warm-up runs, fresh processes like the rest
                for t in tools:
                    r = run_once(t, impl, domain, scale)
                    print(
                        f"[{scale}] {t:16s} warm-up {i + 1}: total {r['total_s']:.2f}s", flush=True
                    )
            for i in range(runs):
                for t in tools:  # interleave tools run-by-run
                    r = run_once(t, impl, domain, scale)
                    raw[t].append(r)
                    print(
                        f"[{scale}] {t:16s} run {i + 1}: total {r['total_s']:.2f}s  "
                        f"gen {r['gen_s']:.2f}s  write {r['write_s']:.2f}s  "
                        f"rss {r['peak_rss_mb']:.0f}MB  load {r['loadavg_before']:.2f}",
                        flush=True,
                    )
            results["scales"][scale] = {
                "summary": {t: summarize(raw[t]) for t in tools},
                "raw": raw,
            }
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text(json.dumps(results, indent=1))
    print_table(results)
    return results


def print_table(results: dict[str, Any]) -> None:
    m = results["meta"]
    for scale, d in results["scales"].items():
        S = d["summary"]
        print(
            f"\n== {m['domain']} {scale}: median of {m['runs']} fresh-process runs, "
            f"{m['cores']} cores"
        )
        print(
            f"{'tool':18s} {'rows':>10s} {'total s':>8s} {'gen s':>7s} {'write s':>8s} "
            f"{'rows/s total':>13s} {'rows/s gen':>12s} {'peak RSS MB':>12s}"
        )
        for t, s in S.items():
            print(
                f"{t:18s} {s['rows']:>10,} {s['total_s']:>8.2f} {s['gen_s']:>7.2f} "
                f"{s['write_s']:>8.2f} {s['rows_per_s_total']:>13,.0f} "
                f"{s['rows_per_s_generate']:>12,.0f} {s['peak_rss_mb']:>12.0f}"
            )
        impl = m["impl"]
        if "baseline" in S and impl in S:
            line = f"speedup ({impl} vs baseline): total "
            line += f"{S['baseline']['total_s'] / S[impl]['total_s']:.2f}x"
            if S[impl]["write_s"] > 0:  # an impl that overlaps generate and write reports no split
                line += (
                    f", generate {S['baseline']['gen_s'] / S[impl]['gen_s']:.2f}x, write "
                    f"{S['baseline']['write_s'] / S[impl]['write_s']:.2f}x"
                )
            print(line)


def main(argv: list[str] | None = None) -> int:
    global BASELINE_ONLY
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--impl", choices=["shape"], required=True)
    ap.add_argument("--domain", default="retail")
    ap.add_argument("--scales", default="medium")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument(
        "--warmup", type=int, default=0, help="discarded runs per tool before the timed ones"
    )
    ap.add_argument(
        "--report",
        default=None,
        help="results JSON (default: $BENCH_OUT_DIR/bench/<impl>_<domain>.json)",
    )
    ap.add_argument("--baseline-only", action="store_true")
    a = ap.parse_args(argv)
    BASELINE_ONLY = a.baseline_only
    report = Path(a.report) if a.report else BENCH_OUT_DIR / "bench" / f"{a.impl}_{a.domain}.json"
    try:
        bench(a.impl, a.domain, a.scales.split(","), a.runs, report, a.warmup)
    except generate.Unsupported as e:
        print(f"unsupported: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
