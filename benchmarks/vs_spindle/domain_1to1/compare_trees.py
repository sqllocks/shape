"""Two source trees of Shape, timed against each other in one sitting.

    source scripts/env.sh && "$SHAPE_VENV/bin/python" \\
        benchmarks/vs_spindle/domain_1to1/compare_trees.py --old /path/to/old/src \\
        --domains retail,hr --scale medium --pairs 12 --report out.json

``--old`` is the ``src`` directory of another checkout (a git worktree of the commit to compare
with, built with its own native kernel); the current tree is the other side. When the two trees'
plugins differ too, give the old checkout's ``plugins/shape-domains/src`` as well, joined with the
platform's path separator (``--old old/src:old/plugins/shape-domains/src``). Each timed run is a
fresh process of ``generate.py`` (the harness's own timed region: load the domain, build the
engine, generate, write Parquet, imports excluded), alternating old and new run by run and domain
by domain, so a machine that is faster or slower at one time of the day is the same for both.
That is what makes this a measure of the change itself; the ratios against the baseline tool of
``bench.py`` are measured at different times and move with the baseline's spread.

The runs of the two trees write to separate output directories (``BENCH_OUT_DIR`` per side), the
whole run holds the exclusive benchmark lock, and each run waits for a 1-minute load average
<= 1.5. The report has every raw run, the median and minimum of each side, and CPU seconds.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from common import bench_lock, machine_meta, wait_for_quiet  # noqa: E402
from paths import SHAPE_PY  # noqa: E402

GENERATE = HERE / "generate.py"
IMPL_SEED = 1042


def run_once(domain: str, scale: str, src: Path | None, out_dir: Path) -> dict[str, float]:
    env = {**os.environ, "BENCH_OUT_DIR": str(out_dir)}
    if src is not None:
        env["PYTHONPATH"] = (
            f"{src}{os.pathsep}{env['PYTHONPATH']}" if env.get("PYTHONPATH") else str(src)
        )
    done = subprocess.run(
        [
            str(SHAPE_PY),
            str(GENERATE),
            "--impl",
            "shape",
            "--domain",
            domain,
            "--scale",
            scale,
            "--seed",
            str(IMPL_SEED),
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    lines = [ln for ln in done.stdout.splitlines() if ln.startswith("GEN_JSON ")]
    if done.returncode != 0 or not lines:
        raise RuntimeError(f"{domain} {scale} failed: {done.stderr[-400:]}")
    record: dict[str, float] = json.loads(lines[-1][len("GEN_JSON ") :])
    return record


def compare(old_src: Path, domains: list[str], scale: str, pairs: int, report: Path) -> None:
    results: dict[str, dict[str, list[dict[str, float]]]] = {
        d: {"old": [], "new": []} for d in domains
    }
    with tempfile.TemporaryDirectory(prefix="compare_trees_") as tmp, bench_lock():
        outs = {"old": Path(tmp) / "old", "new": Path(tmp) / "new"}
        for i in range(pairs + 1):  # the first pair is the discarded warm-up
            for domain in domains:
                for side, src in (("old", old_src), ("new", None)):
                    wait_for_quiet()
                    record = run_once(domain, scale, src, outs[side])
                    if i:
                        results[domain][side].append(record)
    summary = {}
    lines = [
        f"{'domain':<16}{'old median':>12}{'new median':>12}{'change':>9}{'old min':>10}"
        f"{'new min':>10}{'old cpu s':>11}{'new cpu s':>11}"
    ]
    for domain, sides in results.items():
        stats = {}
        for side, runs in sides.items():
            total = [r["total_s"] for r in runs]
            stats[side] = {
                "runs_s": total,
                "median_s": statistics.median(total),
                "min_s": min(total),
                "cpu_median_s": statistics.median(r["cpu_s"] for r in runs),
            }
        summary[domain] = stats
        o, n = stats["old"], stats["new"]
        lines.append(
            f"{domain:<16}{o['median_s'] * 1e3:>10.1f}ms{n['median_s'] * 1e3:>10.1f}ms"
            f"{(n['median_s'] / o['median_s'] - 1) * 100:>+8.1f}%{o['min_s'] * 1e3:>8.1f}ms"
            f"{n['min_s'] * 1e3:>8.1f}ms{o['cpu_median_s']:>11.3f}{n['cpu_median_s']:>11.3f}"
        )
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(
        json.dumps(
            {
                "meta": machine_meta(),
                "old_src": str(old_src),
                "scale": scale,
                "pairs": pairs,
                "results": summary,
            },
            indent=1,
        )
        + "\n"
    )
    report.with_suffix(".txt").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--old",
        required=True,
        help="src directory of the old checkout (more directories, joined with ':' on Linux)",
    )
    ap.add_argument("--domains", required=True)
    ap.add_argument("--scale", default="medium")
    ap.add_argument("--pairs", type=int, default=12)
    ap.add_argument("--report", type=Path, required=True)
    a = ap.parse_args(argv)
    compare(
        Path(os.pathsep.join(str(Path(p).resolve()) for p in a.old.split(os.pathsep))),
        a.domains.split(","),
        a.scale,
        a.pairs,
        a.report,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
