"""Pooled distribution check of a table's columns over many seeds (baseline venv).

    "$REFENGINE_PY" benchmarks/vs_refengine/domain_1to1/pooled_check.py --domain real_estate \\
        --scale small --table neighborhood --out pooled.json

Pools every row of ``--table`` over the baseline seeds 43..42+N and the implementation seeds
1042..1041+N (runs must exist: see ``seed_study_wide.py``) and compares each column's pooled
sample between the tools: two-sample KS (numeric) or a chi-square test of homogeneity
(categorical), Bonferroni over the columns, plus the per-run distinct count of each column and
its two-sample test. Draws within a run are not independent for a fixed-size table, but
different seeds are, so the pooled sample is a sample of the generator's marginal.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from paths import BENCH_OUT_DIR  # noqa: E402


def load(impl: str, domain: str, scale: str, table: str, seeds) -> list[pd.DataFrame]:
    return [
        pd.read_parquet(BENCH_OUT_DIR / impl / domain / scale / f"seed{s}" / f"{table}.parquet")
        for s in seeds
    ]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--domain", required=True)
    ap.add_argument("--scale", required=True)
    ap.add_argument("--table", required=True)
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--alpha", type=float, default=0.05)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    sp = load("refengine", a.domain, a.scale, a.table, range(43, 43 + a.n))
    im = load("shape", a.domain, a.scale, a.table, range(1042, 1042 + a.n))
    cols = list(sp[0].columns)
    res = []
    for c in cols:
        x, y = pd.concat([d[c] for d in sp]), pd.concat([d[c] for d in im])
        row: dict = {"column": c, "pooled_rows": [len(x), len(y)]}
        if pd.api.types.is_numeric_dtype(x) and pd.api.types.is_numeric_dtype(y):
            xv, yv = x.dropna().to_numpy(float), y.dropna().to_numpy(float)
            row["test"] = "ks"
            row["p"] = float(stats.ks_2samp(xv, yv).pvalue)
            row["mean"] = [float(xv.mean()), float(yv.mean())]
            row["sd"] = [float(xv.std()), float(yv.std())]
            row["share_at_min"] = [float(np.mean(xv == xv.min())), float(np.mean(yv == yv.min()))]
            row["share_at_max"] = [float(np.mean(xv == xv.max())), float(np.mean(yv == yv.max()))]
        else:
            cx, cy = x.astype(str).value_counts(), y.astype(str).value_counts()
            cats = sorted(set(cx.index) | set(cy.index))
            tab = np.array([[cx.get(k, 0) for k in cats], [cy.get(k, 0) for k in cats]])
            tab = tab[:, tab.sum(0) > 0]
            row["test"] = "chi2_homogeneity"
            row["categories"] = len(cats)
            row["p"] = float(stats.chi2_contingency(tab)[1]) if tab.shape[1] > 1 else 1.0
        dx = np.array([d[c].nunique() for d in sp], float)
        dy = np.array([d[c].nunique() for d in im], float)
        row["distinct_per_run"] = {"refengine": dx.tolist(), "shape": dy.tolist()}
        row["distinct_mean"] = [float(dx.mean()), float(dy.mean())]
        row["distinct_p_mw"] = (
            1.0
            if np.ptp(np.r_[dx, dy]) == 0
            else float(stats.mannwhitneyu(dx, dy, alternative="two-sided").pvalue)
        )
        res.append(row)
    m = len(res)
    for r in res:
        r["p_bonf"] = min(1.0, r["p"] * m)
        r["distinct_p_bonf"] = min(1.0, r["distinct_p_mw"] * m)
        r["significant"] = bool(r["p_bonf"] < a.alpha or r["distinct_p_bonf"] < a.alpha)
    Path(a.out).write_text(json.dumps({"table": a.table, "columns": res}, indent=1) + "\n", "utf-8")
    for r in res:
        print(
            f"{a.domain}/{a.scale}/{a.table}.{r['column']:20s} {r['test']:16s} p={r['p']:.3g} "
            f"distinct {r['distinct_mean'][0]:.1f}/{r['distinct_mean'][1]:.1f} "
            f"p={r['distinct_p_mw']:.3g}{'  SIGNIFICANT' if r['significant'] else ''}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
