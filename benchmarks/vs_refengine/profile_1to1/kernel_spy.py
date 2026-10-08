"""Where does the time go when the fused kernel profiles a dataset? (T-28, P1-06)

    source scripts/env.sh && "$SHAPE_VENV/bin/python" \
        benchmarks/vs_refengine/profile_1to1/kernel_spy.py d2.csv [--mode exact|bounded] [--spy]

Reads the dataset with ``shape.io`` and profiles it with one ``ProfileState.update`` call per
record batch, then reports the share of samples spent in Python frames.

* default: ``cProfile``; the share is the time in pure-Python frames (``.py`` files) over the
  wall time of the profiled region (Rust time shows up inside the built-in ``update`` and
  ``finalize`` calls, and Arrow reading inside pyarrow built-ins);
* ``--spy``: re-run under ``py-spy record --native`` (needs ptrace; see T-28) and, among the
  samples taken while ``run()`` executes (not interpreter start-up or imports), count those
  whose innermost frame is Python.

Exit code 1 if the Python share is 5% or more.
"""

from __future__ import annotations

import argparse
import cProfile
import pstats
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from paths import PROFILE_DATA_DIR  # noqa: E402

from shape.io import PANDAS_CSV, open_source  # noqa: E402
from shape.kernel.dispatch import get_kernel  # noqa: E402

LIMIT = 0.05


def run(path: Path, mode: str) -> int:
    csv = path.suffix == ".csv"
    src = open_source(path, csv=PANDAS_CSV if csv else None, batch_size=1 << 18)
    state = get_kernel().ProfileState(src.schema, mode)
    for batch in src.batches():
        state.update(batch)
    return len(state.finalize()["columns"])


def python_share(stats: pstats.Stats) -> tuple[float, float]:
    total = stats.total_tt
    py = sum(tt for (fname, _, _), (_, _, tt, _, _) in stats.stats.items() if fname.endswith(".py"))
    return py, total


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("dataset")
    ap.add_argument("--mode", choices=["exact", "bounded"], default="exact")
    ap.add_argument("--spy", action="store_true", help="measure with py-spy --native instead")
    ap.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    path = PROFILE_DATA_DIR / a.dataset
    if not path.exists():
        print(f"missing {path}; run datasets.py", file=sys.stderr)
        return 2
    if a.worker:
        run(path, a.mode)
        return 0
    if a.spy:
        out = Path(f"spy_{a.dataset}.raw")
        cmd = [
            "py-spy",
            "record",
            "--native",
            "-r",
            "200",
            "-f",
            "raw",
            "-o",
            str(out),
            "--",
            sys.executable,
            __file__,
            a.dataset,
            "--mode",
            a.mode,
            "--worker",
        ]
        r = subprocess.run(cmd)
        if r.returncode != 0:
            print("py-spy failed (ptrace not permitted?); use the cProfile mode", file=sys.stderr)
            return 2
        py = tot = 0
        for line in out.read_text().splitlines():
            stack, _, n = line.rpartition(" ")
            frames = stack.split(";")
            # only the profiled region: interpreter start-up and imports are not the work
            if not any(f.startswith("run (") and "kernel_spy.py" in f for f in frames):
                continue
            tot += int(n)
            if ".py:" in frames[-1]:
                py += int(n)
        out.unlink()
        share = py / tot if tot else 0.0
        print(f"{a.dataset}: {py}/{tot} samples in Python frames = {share:.1%}")
        return 0 if share < LIMIT else 1
    prof = cProfile.Profile()
    t0 = time.perf_counter()
    prof.enable()
    ncols = run(path, a.mode)
    prof.disable()
    wall = time.perf_counter() - t0
    py, total = python_share(pstats.Stats(prof))
    share = py / total if total else 0.0
    print(
        f"{a.dataset} [{a.mode}] {ncols} columns, {wall:.2f}s wall: "
        f"pure-Python frames {py:.3f}s of {total:.3f}s profiled = {share:.1%}"
    )
    return 0 if share < LIMIT else 1


if __name__ == "__main__":
    raise SystemExit(main())
