"""A null-rate or distinct-ratio miss of ``verify.py`` clause (b-e) against the study (P6-01e-seed).

    "$SPINDLE_PY" benchmarks/vs_spindle/domain_1to1/column_miss_check.py \\
        --wide wide_composite_healthcare_system_small.json --table healthcare_patient --column email

Reads ``seed_study_wide_stream.py`` output (``raw``: every column's null rate and distinct count per
seed). It reports each tool's distribution of the null rate and of the distinct count over the
same seeds (mean, sd, range), two-sample Mann-Whitney and KS tests, the binomial standard
deviation a null rate of that size would have if rows were independent, and the share of each
tool's seeds outside the verifier's tolerances. Those tolerances are ``verify.py``'s:
``|null_ref - null| <= max(5 sqrt(p(1-p)(1/n+1/m)), 1.5 * B)`` and
``|distinct / distinct_ref - 1| <= max(0.02, 1.5 * B)``, B being the largest deviation of the
baseline's seeds 43-46 from the reference (seed 42). Nothing here is a clause.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
from scipy import stats


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--wide", type=Path, required=True)
    ap.add_argument("--table", required=True)
    ap.add_argument("--column", required=True)
    a = ap.parse_args(argv)
    study = json.loads(a.wide.read_text("utf-8"))
    raw, t, c = study["raw"], a.table, a.column
    n = study["rows"][t]
    ref = raw["reference"][t][c]
    bl = {s: raw["spindle"][s][t][c] for s in raw["spindle"]}
    sh = {s: raw["impl_runs"][s][t][c] for s in raw["impl_runs"]}
    floor_seeds = ("43", "44", "45", "46")
    rows = [("null_rate", "null rate"), ("distinct", "distinct count")]
    for key, label in rows:
        x = np.array([v[key] for s, v in bl.items()], dtype=float)
        y = np.array([v[key] for v in sh.values()], dtype=float)
        pm = float(stats.mannwhitneyu(x, y).pvalue) if np.ptp(np.concatenate([x, y])) else 1.0
        pk = float(stats.ks_2samp(x, y).pvalue) if np.ptp(np.concatenate([x, y])) else 1.0
        print(
            f"{t}.{c} {label}: reference {ref[key]:.4f}; baseline {x.mean():.4f}+-"
            f"{x.std(ddof=1):.4f} [{x.min():.4f}, {x.max():.4f}]; Shape {y.mean():.4f}+-"
            f"{y.std(ddof=1):.4f} [{y.min():.4f}, {y.max():.4f}], 1042 = {sh['1042'][key]:.4f}; "
            f"MW p {pm:.3f}, KS p {pk:.3f}"
        )
    p = max(ref["null_rate"], 1 / n)
    print(f"binomial sd of one run's null rate: {math.sqrt(p * (1 - p) / n):.5f}")
    b_null = max(abs(bl[s]["null_rate"] - ref["null_rate"]) for s in floor_seeds)
    tol_null = max(5 * math.sqrt(p * (1 - p) * (2 / n)), 1.5 * b_null)
    b_dist = max(abs(bl[s]["distinct"] / ref["distinct"] - 1) for s in floor_seeds)
    tol_dist = max(0.02, 1.5 * b_dist)
    fresh = [v for s, v in bl.items() if int(s) >= 47]
    for name, f, tol in (
        ("null rate", lambda v: abs(v["null_rate"] - ref["null_rate"]), tol_null),
        ("distinct ratio", lambda v: abs(v["distinct"] / ref["distinct"] - 1), tol_dist),
    ):
        fb = sum(f(v) > tol for v in fresh)
        fs = sum(f(v) > tol for v in sh.values())
        odds = stats.fisher_exact([[fb, len(fresh) - fb], [fs, len(sh) - fs]]).pvalue
        print(
            f"outside the verifier's {name} tolerance ({tol:.4f}): fresh baseline seeds "
            f"{fb}/{len(fresh)}, Shape seeds {fs}/{len(sh)} (Fisher p {odds:.2f}); "
            f"seed 1042: {f(sh['1042']):.4f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
