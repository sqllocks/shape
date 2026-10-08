"""PROF-CLI and START (section 3.4): wall-clock of the two command lines, start-up included.

    source scripts/env.sh && "$SHAPE_VENV/bin/python" \
        benchmarks/vs_refengine/profile_1to1/bench_cli.py [--reps 5] [dataset.csv ...]

* PROF-CLI, per CSV dataset (default d2.csv d3.csv): RefEngine is
  ``refengine_cli_profile.py <file> -o <out>`` in the RefEngine venv (RefEngine has no command that
  runs ``DataProfiler`` alone); Shape is ``shape profile <file> -o <out>.shape``.
  Runs alternate between the two, fresh process each, median of ``--reps``; the gate is >= 10x.
* START: median of 10 runs of ``shape --version``; the gate is <= 300 ms.

Equivalence first: before any timing the two outputs are compared field by field under T-22, Shape's
through ``adapter.py`` (an adapter in ``benchmarks/``, never in ``src/``); a dataset whose
outputs differ is reported and not timed. The run holds the exclusive benchmark lock. Results go to
``$BENCH_OUT_DIR/profile/cli_bench.json``.
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
from common import bench_lock, machine_meta, wait_for_quiet  # noqa: E402
from paths import BENCH_OUT_DIR, PROFILE_DATA_DIR, REFENGINE_PY, SHAPE_PY  # noqa: E402

SHAPE_CLI = Path(SHAPE_PY).parent / "shape"
OUT = BENCH_OUT_DIR / "profile" / "cli_bench.json"
PROF_CLI_GATE = 10.0
START_GATE_MS = 300.0


def timed(cmd: list[object]) -> float:
    wait_for_quiet()
    t0 = time.perf_counter()
    done = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, check=False)
    elapsed = time.perf_counter() - t0
    if done.returncode != 0:
        raise RuntimeError(f"{cmd} exited {done.returncode}: {done.stderr[-400:]}")
    return elapsed


def refengine_cmd(src: Path, out: Path) -> list[object]:
    return [REFENGINE_PY, HERE / "refengine_cli_profile.py", src, "-o", out]


def shape_cmd(src: Path, out: Path) -> list[object]:
    return [SHAPE_CLI, "profile", src, "-o", out]


def equivalent(src: Path, work: Path) -> list[str]:
    import verify
    from adapter import profile_json

    a, b = work / "refengine.json", work / "shape.shape"
    timed(refengine_cmd(src, a))
    timed(shape_cmd(src, b))
    found: list[str] = []
    verify.check_table(json.loads(a.read_text()), profile_json(b), src.name, {}, found)
    return found


def prof_cli(name: str, reps: int) -> dict[str, object]:
    src = PROFILE_DATA_DIR / name
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        bad = equivalent(src, work)
        if bad:
            return {"dataset": name, "verifier": "fail", "mismatches": bad[:10]}
        sp, sh = [], []
        for _ in range(reps):  # interleaved
            sp.append(timed(refengine_cmd(src, work / "s.json")))
            sh.append(timed(shape_cmd(src, work / "h.shape")))
    ms, mh = statistics.median(sp), statistics.median(sh)
    return {
        "dataset": name,
        "verifier": "pass",
        "refengine_s": ms,
        "shape_s": mh,
        "speedup": ms / mh,
        "gate": PROF_CLI_GATE,
        "passed": ms / mh >= PROF_CLI_GATE,
        "runs_refengine_s": sp,
        "runs_shape_s": sh,
    }


def start_time(runs: int = 10) -> dict[str, object]:
    times = [timed([SHAPE_CLI, "--version"]) * 1000 for _ in range(runs)]
    med = statistics.median(times)
    return {
        "median_ms": med,
        "runs_ms": times,
        "gate_ms": START_GATE_MS,
        "passed": med <= START_GATE_MS,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("datasets", nargs="*", default=["d2.csv", "d3.csv"])
    ap.add_argument("--reps", type=int, default=5)
    args = ap.parse_args(argv)
    missing = [d for d in args.datasets if not (PROFILE_DATA_DIR / d).exists()]
    if missing:
        print(f"missing datasets under {PROFILE_DATA_DIR}: {missing}", file=sys.stderr)
        return 2
    with bench_lock():
        results = {
            "machine": machine_meta(),
            "start": start_time(),
            "prof_cli": [prof_cli(d, args.reps) for d in args.datasets],
        }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(results, indent=1, default=str), encoding="utf-8")
    s = results["start"]
    print(f"START: median {s['median_ms']:.0f} ms (gate {START_GATE_MS:.0f})")  # type: ignore[index]
    ok = bool(s["passed"])  # type: ignore[index]
    for r in results["prof_cli"]:  # type: ignore[attr-defined]
        if r["verifier"] != "pass":
            print(f"PROF-CLI {r['dataset']}: verifier FAILED {r['mismatches'][:3]}")
            ok = False
            continue
        print(
            f"PROF-CLI {r['dataset']}: refengine {r['refengine_s']:.2f}s, "
            f"shape {r['shape_s']:.2f}s, {r['speedup']:.1f}x (gate {PROF_CLI_GATE:.0f}x)"
        )
        ok = ok and bool(r["passed"])
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
