"""Measure Shape's own profiling speed, memory and start-up time (talk numbers).

    source scripts/env.sh && "$SHAPE_VENV/bin/python" benchmarks/measure_product.py

Times ``shape.profile`` itself (the released exact mode) on the deterministic profiling
datasets in ``$BENCH_DATA_DIR/profile``. Create them with the ``datasets.py`` script in the
``profile_1to1`` folder of the benchmark harness. Each measurement runs in a fresh process,
and the profile output of the RUNS runs is hashed and compared. The timing is the median of
RUNS runs, and wall-clock excludes ``import shape``. Peak memory is the process's peak
resident set size. Nothing is written unless the profile is the same on every run and has the
expected rows. Everything runs under the exclusive benchmark lock, after the load gate (plan 1.4).
Writes benchmarks/baselines/2026-09-30-product/product_bench.json. Nothing is extrapolated.
"""

from __future__ import annotations

import json
import os
import platform
import statistics
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "benchmarks" / "vs_refengine"))
from common import bench_lock, wait_for_quiet  # noqa: E402

DATA = Path(os.environ.get("BENCH_DATA_DIR", Path.home() / "bench-data")) / "profile"
OUT = REPO / "benchmarks" / "baselines" / "2026-09-30-product" / "product_bench.json"
RUNS = 3
STARTUP_RUNS = 7
DATASETS = [
    ("d1.parquet", 200_000, 6),
    ("d2.parquet", 1_000_000, 20),
    ("d2.csv", 1_000_000, 20),
    ("d3.parquet", 5_000_000, 10),
    ("d4.parquet", 100_000, 200),
]

CHILD = """
import json, sys, time
sys.path.insert(0, sys.argv[2])
from common import peak_rss_mb  # MB on every platform
import shape
t = time.perf_counter()
p = shape.profile(sys.argv[1], name="bench")
dt = time.perf_counter() - t
rss = peak_rss_mb()
import hashlib
digest = hashlib.sha256(json.dumps(p.to_dict(), sort_keys=True, default=str).encode()).hexdigest()
print(json.dumps({"s": dt, "peak_rss_mb": rss, "rows": p.summary()["row_count"], "sha256": digest}))
"""


def _child(path: Path) -> dict:
    out = subprocess.run(
        [sys.executable, "-c", CHILD, str(path), str(REPO / "benchmarks" / "vs_refengine")],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(out.stdout.strip().splitlines()[-1])


def _timed(cmd: list[str]) -> float:
    t = time.perf_counter()
    subprocess.run(cmd, check=True, capture_output=True)
    return time.perf_counter() - t


def main() -> None:
    result: dict = {
        "_meta": {
            "cores": os.cpu_count(),
            "platform": platform.platform(),
            "python": platform.python_version(),
            "started": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "runs": RUNS,
        },
        "profile": {},
    }
    with bench_lock():
        _measure(result)
    print(result["startup"])
    OUT.write_text(json.dumps(result, indent=1) + "\n")


def _measure(result: dict) -> None:
    for name, rows, cols in DATASETS:
        runs = []
        for _ in range(RUNS):
            wait_for_quiet()
            runs.append(_child(DATA / name))
        wrong = sorted({r["rows"] for r in runs if r["rows"] != rows})
        if wrong:
            raise SystemExit(f"{name}: profiled {wrong[0]} rows, expected {rows}; nothing written")
        identical = len({r["sha256"] for r in runs}) == 1
        if not identical:
            raise SystemExit(f"{name}: the profile differs between runs; nothing written")
        med = statistics.median(r["s"] for r in runs)
        result["profile"][name] = {
            "rows": rows,
            "columns": cols,
            "median_s": med,
            "runs_s": [r["s"] for r in runs],
            "peak_rss_mb": statistics.median(r["peak_rss_mb"] for r in runs),
            "rows_per_s": rows / med,
            "output_identical_across_runs": identical,
        }
        print(name, round(med, 2), "s", round(rows / med), "rows/s")
    shape_bin = Path(sys.executable).parent / "shape"
    wait_for_quiet()
    result["startup"] = {
        "runs": STARTUP_RUNS,
        "import_shape_median_s": statistics.median(
            _timed([sys.executable, "-c", "import shape"]) for _ in range(STARTUP_RUNS)
        ),
        "cli_version_cmd_median_s": statistics.median(
            _timed([str(shape_bin), "version"]) for _ in range(STARTUP_RUNS)
        ),
        "python_bare_median_s": statistics.median(
            _timed([sys.executable, "-c", "pass"]) for _ in range(STARTUP_RUNS)
        ),
    }


if __name__ == "__main__":
    main()
