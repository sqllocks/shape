"""Bounded mode keeps memory flat: peak RSS on D3 at 5M rows against 50M rows (P1-07).

    source scripts/env.sh && "$SHAPE_VENV/bin/python" \
        benchmarks/vs_spindle/profile_1to1/rss_check.py [--small 5000000] [--large 50000000]

Generates D3 at both sizes (``datasets.py D3 --rows N``, into a scratch directory under
``$BENCH_OUT_DIR``), profiles each CSV in bounded mode in a fresh process, and compares the
peak RSS of the two processes. Exits 1 if they differ by 10% or more.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from paths import BENCH_OUT_DIR, SHAPE_PY  # noqa: E402

LIMIT = 0.10
CHILD = """
import json, resource, sys, time
from shape.profile.engine import profile
t0 = time.perf_counter()
doc = profile(sys.argv[1], mode="bounded")
t = next(iter(doc["tables"].values()))
print(json.dumps({"rows": t["rows"], "seconds": time.perf_counter() - t0,
                  "peak_rss_mb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024}))
"""


def measure(rows: int, scratch: Path) -> dict:
    data = scratch / f"d3_{rows}"
    data.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "PROFILE_DATA_DIR": str(data)}
    subprocess.run(
        [str(SHAPE_PY), str(HERE / "datasets.py"), "D3", "--rows", str(rows)],
        check=True,
        env=env,
        stdout=subprocess.DEVNULL,
    )
    try:
        r = subprocess.run(
            [str(SHAPE_PY), "-c", CHILD, str(data / "d3.csv")],
            check=True,
            capture_output=True,
            text=True,
        )
    finally:
        shutil.rmtree(data, ignore_errors=True)
    out = json.loads(r.stdout.strip().splitlines()[-1])
    print(
        f"{rows:>12,} rows: peak RSS {out['peak_rss_mb']:.0f} MB, {out['seconds']:.1f}s", flush=True
    )
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--small", type=int, default=5_000_000)
    ap.add_argument("--large", type=int, default=50_000_000)
    a = ap.parse_args(argv)
    scratch = BENCH_OUT_DIR / f"rss_check_{int(time.time())}"
    scratch.mkdir(parents=True)
    try:
        small, large = measure(a.small, scratch), measure(a.large, scratch)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    diff = abs(large["peak_rss_mb"] - small["peak_rss_mb"]) / small["peak_rss_mb"]
    print(f"peak RSS differs by {diff:.1%} (limit {LIMIT:.0%})")
    return 0 if diff < LIMIT else 1


if __name__ == "__main__":
    raise SystemExit(main())
