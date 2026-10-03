"""GEN-CLI: the two command lines, end to end, start-up included (plan section 3.4, T-19 secondary).

    source scripts/env.sh && "$SHAPE_VENV/bin/python" \\
        benchmarks/vs_spindle/domain_1to1/bench_cli.py --domain retail --scales medium,large \\
        --runs 5 --warmup 1

Each timed run is a fresh process of

    $SPINDLE_VENV/bin/spindle generate DOMAIN --scale S --seed 42   --format parquet -o DIR
    $SHAPE_VENV/bin/shape     generate DOMAIN --scale S --seed 1042 --format parquet -o DIR

(wall clock around the whole process: interpreter start, imports, generation, writing). The output
goes to the ``<impl>-cli`` layout directories (``spindle-cli``, ``shape-cli``), next to the API
runs, so ``verify.py --impl shape --cli`` checks exactly what the CLI wrote (equivalence first:
run it, require exit 0, then trust the timings). Default threading for both; ``--shape-threads 1``
also times single-threaded Shape. The whole run holds the exclusive benchmark lock and each run
waits for a 1-minute load average <= 1.5. The output directory is removed before every run, so
no cache survives between runs.
Tools are interleaved run by run; the median is reported next to every raw run.
"""

from __future__ import annotations

import argparse
import json
import os
import resource
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import generate  # noqa: E402
from common import bench_lock, machine_meta, wait_for_quiet  # noqa: E402
from paths import BENCH_OUT_DIR, SHAPE_VENV, SPINDLE_VENV  # noqa: E402

REF_SEED = 42
IMPL_SEED = 1042


def command(tool: str, domain: str, scale: str, dest: Path) -> list[str]:
    if tool == "spindle":
        exe, seed = SPINDLE_VENV / "bin" / "spindle", REF_SEED
    else:
        exe, seed = SHAPE_VENV / "bin" / "shape", IMPL_SEED
    return [
        str(exe),
        "generate",
        domain,
        "--scale",
        scale,
        "--seed",
        str(seed),
        "--format",
        "parquet",
        "-o",
        str(dest),
    ]


def run_once(tool: str, domain: str, scale: str, threads: int | None) -> dict:
    """One fresh-process run; the output replaces the tool's layout directory afterwards."""
    impl = "spindle" if tool == "spindle" else "shape"
    seed = REF_SEED if tool == "spindle" else IMPL_SEED
    # CLI runs live next to the API runs (``<impl>-cli``), never in their place: the API reference
    # runs record the generation order of the tables, a CLI run only the files it wrote.
    final = generate.out_dir(f"{impl}-cli", domain, scale, seed)
    tmp = final.with_name(final.name + f".tmp{os.getpid()}")
    shutil.rmtree(tmp, ignore_errors=True)  # no cache between runs
    load = wait_for_quiet()
    env = dict(os.environ)
    if threads is not None and tool != "spindle":
        env["SHAPE_THREADS"] = str(threads)
    cmd = command(tool, domain, scale, tmp)
    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    t0 = time.perf_counter()
    done = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=BENCH_OUT_DIR)
    wall = time.perf_counter() - t0
    after = resource.getrusage(resource.RUSAGE_CHILDREN)
    if done.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} failed ({done.returncode}):\n{done.stderr[-2000:]}")
    import pyarrow.parquet as pq  # noqa: PLC0415  (after the timed region)

    rows = {p.stem: pq.read_metadata(p).num_rows for p in sorted(tmp.glob("*.parquet"))}
    (tmp / generate.SUCCESS).write_text(
        json.dumps(
            {
                "impl": impl,
                "domain": domain,
                "scale": scale,
                "seed": seed,
                "rows": rows,
                "via": "cli",
            }
        )
    )
    shutil.rmtree(final, ignore_errors=True)
    tmp.rename(final)
    return {
        "wall_s": wall,
        "cpu_s": (after.ru_utime + after.ru_stime) - (before.ru_utime + before.ru_stime),
        "rows": sum(rows.values()),
        "loadavg_before": load,
        "cmd": " ".join(cmd),
    }


def median(runs: list[dict], key: str) -> float:
    return statistics.median(r[key] for r in runs)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--domain", default="retail")
    ap.add_argument("--scales", default="medium,large")
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--warmup", type=int, default=1)
    ap.add_argument(
        "--shape-threads", type=int, default=None, help="SHAPE_THREADS for the Shape runs"
    )
    ap.add_argument("--report", default=None)
    a = ap.parse_args(argv)
    label = "shape" if a.shape_threads is None else f"shape_threads{a.shape_threads}"
    report = (
        Path(a.report) if a.report else BENCH_OUT_DIR / "bench" / f"cli_{label}_{a.domain}.json"
    )
    out: dict = {
        "meta": {
            **machine_meta(),
            "domain": a.domain,
            "runs": a.runs,
            "warmup": a.warmup,
            "shape_threads": a.shape_threads,
            "loadavg_start": os.getloadavg()[0],
        },
        "scales": {},
    }
    tools = ["spindle", "shape"]
    with bench_lock():
        for scale in a.scales.split(","):
            raw: dict[str, list[dict]] = {t: [] for t in tools}
            for i in range(a.warmup):
                for t in tools:
                    r = run_once(t, a.domain, scale, a.shape_threads)
                    print(f"[{scale}] {t:8s} warm-up {i + 1}: {r['wall_s']:.3f}s", flush=True)
            for i in range(a.runs):
                for t in tools:
                    r = run_once(t, a.domain, scale, a.shape_threads)
                    raw[t].append(r)
                    print(
                        f"[{scale}] {t:8s} run {i + 1}: {r['wall_s']:.3f}s  cpu {r['cpu_s']:.2f}s  "
                        f"load {r['loadavg_before']:.2f}",
                        flush=True,
                    )
            sp, sh = median(raw["spindle"], "wall_s"), median(raw["shape"], "wall_s")
            out["scales"][scale] = {
                "spindle_median_s": sp,
                "shape_median_s": sh,
                "ratio": sp / sh,
                "spindle_runs_s": [round(r["wall_s"], 3) for r in raw["spindle"]],
                "shape_runs_s": [round(r["wall_s"], 3) for r in raw["shape"]],
                "raw": raw,
            }
            print(f"[{scale}] median spindle {sp:.3f}s, shape {sh:.3f}s => {sp / sh:.2f}x")
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
