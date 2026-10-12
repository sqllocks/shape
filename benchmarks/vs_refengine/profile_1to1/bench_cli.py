"""PROF-CLI compares release and candidate commands; START retains its 300 ms gate."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from common import bench_lock, machine_meta, wait_for_quiet  # noqa: E402 (harness path bootstrap)
from paths import (  # noqa: E402 (harness path bootstrap)
    BASELINE_SHAPE,
    BENCH_OUT_DIR,
    PROFILE_DATA_DIR,
    SHAPE_PY,
)
from recorded_store import RecordingError  # noqa: E402 (harness path bootstrap)

SHAPE_CLI = Path(SHAPE_PY).parent / "shape"
OUT = BENCH_OUT_DIR / "profile/cli_bench.json"
START_GATE_MS = 300.0


def execute(cmd: list[Any]) -> None:
    env = dict(os.environ)
    if cmd[0] == BASELINE_SHAPE:
        env.pop("PYTHONPATH", None)
        env.pop("PYTHONHOME", None)
    done = subprocess.run([str(c) for c in cmd], env=env, capture_output=True, text=True)
    if done.returncode:
        raise RecordingError("profile CLI failed (exit " + str(done.returncode) + ")")


def timed(cmd: list[Any]) -> float:
    wait_for_quiet()
    t0 = time.perf_counter()
    execute(cmd)
    return time.perf_counter() - t0


def baseline_cmd(src: Path, out: Path) -> list[Any]:
    return [BASELINE_SHAPE, "profile", src, "-o", out, "--capture", "full"]


def shape_cmd(src: Path, out: Path) -> list[Any]:
    return [SHAPE_CLI, "profile", src, "-o", out, "--capture", "full"]


def equivalent(src: Path, work: Path, *, generate: bool = True) -> list[str]:
    spec = importlib.util.spec_from_file_location(
        "profile_cli_recorded_verifier", HERE / "verify.py"
    )
    assert spec and spec.loader
    v = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(v)
    recorded = v.baseline_profile(src.name)
    a, b = work / "baseline.shape", work / "candidate.shape"
    if generate:
        execute(baseline_cmd(src, a))
        execute(shape_cmd(src, b))
    import shape

    release = v.sd.normalize(shape.load(a))
    candidate = v.sd.normalize(shape.load(b))
    failures: list[str] = []
    v.compare_profile(recorded, release, src.name, {}, failures)
    if failures:
        raise RecordingError("profile:" + src.name + ": fresh release CLI differs from recording")
    v.compare_profile(recorded, candidate, src.name, {}, failures)
    return failures


def prof_cli(name: str, reps: int) -> dict[str, Any]:
    src = PROFILE_DATA_DIR / name
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        bad = equivalent(src, work)
        if bad:
            return {"dataset": name, "verifier": "fail", "mismatches": bad[:10]}
        baseline = []
        candidate = []
        for _ in range(reps):
            baseline.append(timed(baseline_cmd(src, work / "baseline.shape")))
            candidate.append(timed(shape_cmd(src, work / "candidate.shape")))
        # Check the final timed outputs before publishing their numbers.
        bad = equivalent(src, work, generate=False)
        if bad:
            return {"dataset": name, "verifier": "fail", "mismatches": bad[:10]}
    mb, mc = statistics.median(baseline), statistics.median(candidate)
    return {
        "dataset": name,
        "verifier": "pass",
        "baseline_s": mb,
        "shape_s": mc,
        "ratio_vs_baseline": mb / mc,
        "runs_baseline_s": baseline,
        "runs_shape_s": candidate,
    }


def start_time(runs: int = 10) -> dict[str, Any]:
    times = [timed([SHAPE_CLI, "--version"]) * 1000 for _ in range(runs)]
    med = statistics.median(times)
    return {
        "median_ms": med,
        "runs_ms": times,
        "gate_ms": START_GATE_MS,
        "passed": med <= START_GATE_MS,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("datasets", nargs="*", default=["d2.csv", "d3.csv"])
    ap.add_argument("--reps", type=int, default=5)
    a = ap.parse_args(argv)
    if a.reps < 1:
        ap.error("--reps must be positive")
    try:
        with bench_lock():
            # Authenticate every workload before any timing, including START.
            with tempfile.TemporaryDirectory() as tmp:
                for ds in a.datasets:
                    if equivalent(PROFILE_DATA_DIR / ds, Path(tmp)):
                        print("profile:" + ds + ": equivalence failed", file=sys.stderr)
                        return 1
            records = [prof_cli(ds, a.reps) for ds in a.datasets]
            if any(r["verifier"] != "pass" for r in records):
                return 1
            start = start_time()
            results = {"machine": machine_meta(), "start": start, "prof_cli": records}
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(results, sort_keys=True, indent=2) + "\n")
        print(f"START: median {start['median_ms']:.0f} ms (gate {START_GATE_MS:.0f})")
        return 0 if start["passed"] else 1
    except (RecordingError, OSError, ValueError) as exc:
        print(
            str(exc) if isinstance(exc, RecordingError) else "profile CLI input unavailable",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
