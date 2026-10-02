"""P6-02 acceptance: per-mutator parity of ``shape.chaos`` against the pinned baseline's chaos
package, on mutation rates and mutation types.

    source scripts/env.sh && python benchmarks/vs_spindle/chaos_1to1/verify.py [--quick]

Both tools get the same input, the same configuration and the same seeds. Every job runs
(a) each sub-mutation on its own, at the four intensity presets, and (b) each category's
``mutate`` as a whole (the schema category at a day before and after its breaking-change day),
and (c) the engine's injection schedule (``should_inject``) for the three escalations and the
four presets. One classifier (``chaos_common.py``), run on the (input, output) pair of either tool,
reads off which mutations happened and how many rows they changed, so nothing is taken from a
tool's own bookkeeping.

What is compared, and the tolerance (derived, not tuned)
--------------------------------------------------------
* **Types: exact.** The set of mutation kinds seen must be equal in both tools, every kind must
  be a member of the category's taxonomy, and a sub-mutation run on its own must produce only
  its own kind. For a whole-category run a kind counts toward the set when it occurs in at least
  10 runs of either tool (rarer kinds are compared by frequency only).
* **Frequencies** (a kind appears in a run; the engine fires): two-proportion z-test,
  ``|p1 - p2| <= z * sqrt(p (1 - p) (1/N1 + 1/N2))`` with ``p`` pooled. A zero standard error
  demands equality.
* **Mean affected-row rate** of a kind (changed rows / input rows, or changed bytes / input
  bytes): Welch ``|m1 - m2| <= max(z * sqrt(v1/N1 + v2/N2), 1/R)``, where ``R`` is the input's
  row (byte) count, so a difference of one row is never an error.
* ``z`` is the normal quantile for a family-wise error of 0.1% over all ``m`` comparisons of the
  run (Bonferroni): ``z = Phi^-1(1 - 0.001 / (2 m))``. If the two tools drew from the same
  distribution, the harness would fail by chance at most once in a thousand runs.
* The share of runs whose outputs are identical cell for cell is reported, and is not a
  criterion: it depends on both tools' numpy releasing the same random stream.

Exit code 0 when every check passes, 1 otherwise. The baseline checkout is only read (it is
imported in its own venv by ``baseline_worker.py``).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path
from statistics import NormalDist
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import chaos_common as common  # noqa: E402
import chaos_jobs as jobdefs  # noqa: E402
from paths import BENCH_OUT_DIR, SPINDLE_PY  # noqa: E402

ALPHA = 0.001
RARE = 10  # a kind seen in fewer runs than this in both tools is compared by frequency only


class Comparisons:
    """Collects every z-comparison, then judges them with one family-wise ``z``."""

    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []

    def proportion(self, label: str, k1: int, n1: int, k2: int, n2: int) -> None:
        p1, p2 = k1 / n1, k2 / n2
        pooled = (k1 + k2) / (n1 + n2)
        se = math.sqrt(pooled * (1 - pooled) * (1 / n1 + 1 / n2))
        self.items.append(
            {
                "label": label,
                "kind": "proportion",
                "a": p1,
                "b": p2,
                "diff": abs(p1 - p2),
                "se": se,
                "floor": 0.0,
            }
        )

    def mean(self, label: str, xs: list[float], ys: list[float], floor: float) -> None:
        if not xs and not ys:
            return
        if not xs or not ys:
            self.items.append(
                {
                    "label": label,
                    "kind": "mean",
                    "a": None,
                    "b": None,
                    "diff": math.inf,
                    "se": 0.0,
                    "floor": floor,
                }
            )
            return
        v1 = statistics.variance(xs) if len(xs) > 1 else 0.0
        v2 = statistics.variance(ys) if len(ys) > 1 else 0.0
        se = math.sqrt(v1 / len(xs) + v2 / len(ys))
        a, b = statistics.fmean(xs), statistics.fmean(ys)
        self.items.append(
            {
                "label": label,
                "kind": "mean",
                "a": a,
                "b": b,
                "diff": abs(a - b),
                "se": se,
                "floor": floor,
            }
        )

    def judge(self) -> tuple[float, list[dict[str, Any]]]:
        m = max(1, len(self.items))
        z = NormalDist().inv_cdf(1 - ALPHA / (2 * m))
        for it in self.items:
            tol = max(z * it["se"], it["floor"])
            it["tolerance"] = tol
            it["ok"] = it["diff"] <= tol
        return z, self.items


def summarize_runs(runs: list[dict[str, Any]], kinds: list[str] | None = None) -> dict[str, Any]:
    n = len(runs)
    errors = [r["error"] for r in runs if "error" in r]
    good = [r for r in runs if "error" not in r]
    count: dict[str, int] = {}
    rates: dict[str, list[float]] = {}
    for r in good:
        per: dict[str, float] = {}
        for ev in r["events"]:
            per[ev["kind"]] = per.get(ev["kind"], 0.0) + ev["rate"]
        for k, rate in per.items():
            count[k] = count.get(k, 0) + 1
            rates.setdefault(k, []).append(rate)
    return {"n": n, "good": len(good), "errors": errors, "count": count, "rates": rates}


def compare_table_jobs(
    jobs: list[dict[str, Any]], shape: dict[str, Any], base: dict[str, Any], cmp: Comparisons
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for job in jobs:
        jid, cat = job["id"], job["category"]
        a, b = summarize_runs(shape[jid]), summarize_runs(base[jid])
        floor = 1 / (len(common.make_file_bytes()) if cat == "file" else common.N_ROWS)
        problems: list[str] = []
        for side, s in (("shape", a), ("baseline", b)):
            if s["errors"]:
                problems.append(f"{side} raised in {len(s['errors'])} runs: {s['errors'][0]}")
        taxonomy = set(jobdefs.SUB_KINDS[cat])
        if job["level"] == "top":
            # cells hit by two mutations of one call cannot be told apart from the output alone
            taxonomy.add("overlap")
        for side, s in (("shape", a), ("baseline", b)):
            odd = set(s["count"]) - taxonomy
            if odd:
                problems.append(f"{side} produced kinds outside the {cat} taxonomy: {sorted(odd)}")
            if job["level"] == "sub":
                other = set(s["count"]) - {job["kind"]}
                if other:
                    problems.append(f"{side} produced {sorted(other)} instead of {job['kind']}")
        if a["good"] and b["good"]:
            kinds = set(a["count"]) | set(b["count"])
            for k in sorted(kinds):
                ca, cb = a["count"].get(k, 0), b["count"].get(k, 0)
                if (ca >= RARE) != (cb >= RARE) and max(ca, cb) >= RARE and min(ca, cb) == 0:
                    problems.append(f"kind {k!r} seen {ca} times in shape, {cb} in baseline")
                cmp.proportion(f"{jid} freq[{k}]", ca, a["good"], cb, b["good"])
                cmp.mean(f"{jid} rate[{k}]", a["rates"].get(k, []), b["rates"].get(k, []), floor)
        ident = sum(
            1
            for x, y in zip(shape[jid], base[jid], strict=True)
            if "digest" in x and x.get("digest") == y.get("digest")
        )
        rows.append(
            {
                "id": jid,
                "problems": problems,
                "identical": ident,
                "runs": len(shape[jid]),
                "kinds_shape": a["count"],
                "kinds_baseline": b["count"],
            }
        )
    return rows


def compare_sched(
    jobs: list[dict[str, Any]], shape: dict[str, Any], base: dict[str, Any], cmp: Comparisons
) -> list[dict[str, Any]]:
    rows = []
    ncat = len(jobdefs.CATEGORIES)
    buckets = [(0, 8), (8, 38), (38, 68), (68, 100)]
    for job in jobs:
        jid = job["id"]
        problems: list[str] = []
        runs = (shape[jid], base[jid])
        if any("error" in r for rs in runs for r in rs):
            problems.append("a run raised")
            rows.append({"id": jid, "problems": problems, "identical": 0, "runs": len(shape[jid])})
            continue
        if job.get("enabled", True) is False:
            for side, rs in zip(("shape", "baseline"), runs, strict=True):
                if any("1" in r["decisions"] for r in rs):
                    problems.append(f"{side} fired while disabled")
        for ci, cat in enumerate(jobdefs.CATEGORIES):
            for lo, hi in buckets:
                counts = []
                for rs in runs:
                    fired = total = 0
                    for r in rs:
                        for d in range(lo, hi):
                            fired += r["decisions"][d * ncat + ci] == "1"
                            total += 1
                    counts.append((fired, total))
                if lo < 8 and any(c[0] for c in counts):
                    problems.append(f"fired inside the warmup ({cat}, days {lo}-{hi})")
                cmp.proportion(
                    f"{jid} fire[{cat} d{lo}-{hi}]",
                    counts[0][0],
                    counts[0][1],
                    counts[1][0],
                    counts[1][1],
                )
        ov = job.get("override")
        if ov:
            ci = jobdefs.CATEGORIES.index(ov["category"])
            for side, rs in zip(("shape", "baseline"), runs, strict=True):
                if not all(r["decisions"][ov["day"] * ncat + ci] == "1" for r in rs):
                    problems.append(f"{side}: the override did not always fire")
        ident = sum(1 for x, y in zip(*runs, strict=True) if x["decisions"] == y["decisions"])
        rows.append({"id": jid, "problems": problems, "identical": ident, "runs": len(shape[jid])})
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="fewer seeds (a smoke run)")
    ap.add_argument(
        "--negative-control",
        action="store_true",
        help="perturb Shape's mutation rates by 20%% and require the comparison to FAIL (exit 0 "
        "only if it does): proves the harness can detect a rate error",
    )
    ap.add_argument("--report", default=str(BENCH_OUT_DIR / "chaos_1to1_report.json"))
    args = ap.parse_args()
    sub, top, sched = (20, 60, 10) if args.quick else (100, 300, 40)
    jobs = jobdefs.build_jobs(sub, top, sched)

    import shape_side

    if args.negative_control:
        from shape.chaos.categories import Mutator

        jobs = [j for j in jobs if j["level"] == "sub" and j["category"] in ("value", "temporal")]
        Mutator._pick_fraction = staticmethod(  # type: ignore[method-assign]
            lambda base, intensity, cap=0.8: min(base * intensity * 1.2, cap)
        )

    shape_out = shape_side.run_jobs(jobs)
    with tempfile.TemporaryDirectory() as tmp:
        jf, of = Path(tmp) / "jobs.json", Path(tmp) / "out.json"
        jf.write_text(json.dumps(jobs))
        env = {**os.environ, "PYTHONHASHSEED": "0"}
        proc = subprocess.run(
            [str(SPINDLE_PY), str(HERE / "baseline_worker.py"), str(jf), str(of)],
            env=env,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            print(proc.stderr)
            return 1
        base_out = json.loads(of.read_text())

    cmp = Comparisons()
    table_jobs = [j for j in jobs if j["level"] != "sched"]
    sched_jobs = [j for j in jobs if j["level"] == "sched"]
    rows = compare_table_jobs(table_jobs, shape_out, base_out, cmp) + compare_sched(
        sched_jobs, shape_out, base_out, cmp
    )
    z, items = cmp.judge()

    failed = [r for r in rows if r["problems"]]
    bad = [i for i in items if not i["ok"]]
    ident = sum(r["identical"] for r in rows)
    runs = sum(r["runs"] for r in rows)
    print(
        f"jobs: {len(rows)}   runs per tool: {runs}   comparisons: {len(items)}   "
        f"z = {z:.3f} (alpha {ALPHA}, Bonferroni)"
    )
    print(f"outputs identical cell for cell (informational): {ident}/{runs}")
    for r in failed:
        print(f"FAIL {r['id']}: " + "; ".join(r["problems"]))
    for i in bad:
        print(
            f"FAIL {i['label']}: {i['a']} vs {i['b']} "
            f"(diff {i['diff']:.4g} > tolerance {i['tolerance']:.4g})"
        )
    verdict = not failed and not bad
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(
        json.dumps(
            {"z": z, "alpha": ALPHA, "comparisons": items, "jobs": rows, "pass": verdict},
            indent=1,
            default=str,
        )
    )
    print("chaos_1to1:", "PASS" if verdict else "FAIL", f"(report: {args.report})")
    if args.negative_control:
        print("negative control:", "detected the perturbation" if not verdict else "NOT DETECTED")
        return 0 if not verdict else 1
    return 0 if verdict else 1


if __name__ == "__main__":
    sys.exit(main())
