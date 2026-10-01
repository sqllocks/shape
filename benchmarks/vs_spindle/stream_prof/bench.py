"""STREAM-PROF (gate G3): bounded-mode stream profiling of a D2 replay in 64k-row micro-batches,
against Shape batch profiling of D2 in bounded mode. There is no Spindle equivalent (plan 3.4).

    source scripts/env.sh && "$SHAPE_VENV/bin/python" benchmarks/vs_spindle/stream_prof/bench.py \
        [--reps 5] [--batch 65536] [--file d2.parquet] [--out FILE.json]

Equivalence first: `verify.py` must exit 0 (this script runs it, and refuses to time otherwise).

T-19: each measurement is one fresh process after one warm-up run, in-process timing with the
interpreter start-up and imports excluded (the worker imports everything, then times
"read the Parquet file + profile it" with perf_counter), the median of ``--reps`` runs, the
runs of the two sides interleaved, the whole run under the exclusive benchmark lock and the
1-minute load average at most 1.5. Both sides use their default threading (all cores);
``--single-thread`` adds the ``SHAPE_THREADS=1`` pair, which is always worth reporting next
to the default.

The figure is throughput: STREAM-PROF = (rows / stream seconds) / (rows / batch seconds), the
gate is at least 0.80 (stretch 0.95).
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
from paths import BENCH_OUT_DIR, PROFILE_DATA_DIR  # noqa: E402

GATE = 0.80
STRETCH = 0.95


def worker(side: str, path: str, batch_rows: int) -> None:
    """One timed run, in a fresh process; prints ``seconds rows``."""
    import pyarrow.parquet as pq

    from shape.profile.engine import EngineOptions, configure_threads, profile_table
    from shape.streaming.runtime import GlobalProfiler

    configure_threads(None)
    if side == "batch":
        t0 = time.perf_counter()
        entry = profile_table(path, "d2", EngineOptions(mode="bounded"))
        seconds = time.perf_counter() - t0
        rows = entry["rows"]
    else:
        t0 = time.perf_counter()
        pf = pq.ParquetFile(path)
        prof = GlobalProfiler(pf.schema_arrow, name="d2")
        for batch in pf.iter_batches(batch_size=batch_rows):
            prof.process(batch)
        (window,) = prof.finish()
        seconds = time.perf_counter() - t0
        rows = window.rows
    print(json.dumps({"seconds": seconds, "rows": rows}))


def run_once(side: str, path: Path, batch_rows: int, threads: str | None) -> dict:
    env = {**os.environ}
    if threads is not None:
        env["SHAPE_THREADS"] = threads
        env["OMP_NUM_THREADS"] = env["OPENBLAS_NUM_THREADS"] = threads
    cmd = [
        sys.executable,
        __file__,
        "--worker",
        side,
        "--file",
        str(path),
        "--batch",
        str(batch_rows),
    ]
    out = subprocess.run(cmd, env=env, capture_output=True, text=True, check=True).stdout
    return json.loads(out.strip().splitlines()[-1])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--file", default="d2.parquet")
    ap.add_argument("--reps", type=int, default=5)
    ap.add_argument("--batch", type=int, default=65_536)
    ap.add_argument("--single-thread", action="store_true", help="also measure SHAPE_THREADS=1")
    ap.add_argument("--out", default=str(BENCH_OUT_DIR / "stream_prof" / "results.json"))
    ap.add_argument("--worker", choices=["batch", "stream"], help="(internal)")
    ap.add_argument("--skip-verify", action="store_true", help="never for a reported number")
    args = ap.parse_args(argv)
    if args.worker:
        worker(args.worker, args.file, args.batch)
        return 0
    path = PROFILE_DATA_DIR / args.file
    if not path.exists():
        print(f"missing {path}: run profile_1to1/datasets.py D2", file=sys.stderr)
        return 1
    if not args.skip_verify:
        rc = subprocess.run(
            [
                sys.executable,
                str(HERE / "verify.py"),
                "--file",
                args.file,
                "--batch",
                str(args.batch),
            ]
        ).returncode
        if rc != 0:
            print("equivalence failed: no timing is reported (plan 6.4)", file=sys.stderr)
            return 1

    configs = [("default", None)] + ([("single_thread", "1")] if args.single_thread else [])
    results: dict = {"_meta": {**machine_meta(), "reps": args.reps, "batch_rows": args.batch}}
    with bench_lock():
        for label, threads in configs:
            run_once("batch", path, args.batch, threads)  # warm-up (page cache, allocator)
            run_once("stream", path, args.batch, threads)
            runs: dict[str, list[dict]] = {"batch": [], "stream": []}
            for rep in range(args.reps):
                for side in ("batch", "stream"):
                    load = wait_for_quiet()
                    r = run_once(side, path, args.batch, threads)
                    r["load_before"] = load
                    runs[side].append(r)
                    print(
                        f"{label:13s} rep{rep} {side:6s} {r['seconds']:7.3f}s load={load:.2f}",
                        flush=True,
                    )
            med = {s: statistics.median(x["seconds"] for x in v) for s, v in runs.items()}
            rows = runs["batch"][0]["rows"]
            ratio = (rows / med["stream"]) / (rows / med["batch"])
            results[label] = {
                "rows": rows,
                "batch_median_s": med["batch"],
                "stream_median_s": med["stream"],
                "stream_rows_per_s": rows / med["stream"],
                "batch_rows_per_s": rows / med["batch"],
                "ratio": ratio,
                "runs": runs,
                "gate": GATE,
                "pass": ratio >= GATE,
            }
            print(
                f"STREAM-PROF {label}: stream {med['stream']:.3f}s vs batch {med['batch']:.3f}s "
                f"-> {ratio:.1%} of batch throughput (gate {GATE:.0%}, stretch {STRETCH:.0%}): "
                f"{'PASS' if ratio >= GATE else 'FAIL'}",
                flush=True,
            )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2) + "\n")
    print(f"wrote {out}")
    return 0 if all(results[label]["pass"] for label, _ in configs) else 1


if __name__ == "__main__":
    raise SystemExit(main())
