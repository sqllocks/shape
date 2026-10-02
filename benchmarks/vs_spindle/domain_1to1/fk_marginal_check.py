"""Pooled marginal of one foreign-key column over many seeds of each tool (P6-01e-seed).

    source scripts/env.sh && "$SPINDLE_PY" benchmarks/vs_spindle/domain_1to1/fk_marginal_check.py \\
        --domain composite_digital_commerce --scale small --table retail_order \\
        --parent-table retail_customer --column customer_id \\
        --n 30 --out fk.json

Uses the runs ``generate.py`` writes (missing ones are generated, each tool in its own venv). For
every run it takes the column's per-parent row counts; it then compares, over the n baseline seeds
and the n Shape seeds: the pooled count of each parent (a two-sample chi-square test of
homogeneity, parents with a small pooled count merged), the per-run summary statistics (largest
parent share, share of the top 10 parents, number of distinct parents, mean parent rank) with a
Mann-Whitney test each, and the mean rank. Nothing here is a clause of ``verify.py``.
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
sys.path.insert(0, str(HERE))
import generate  # noqa: E402
import verify  # noqa: E402


def run_stats(col: pd.Series, parents: int) -> tuple[np.ndarray, dict[str, float]]:
    v = col.dropna().to_numpy()
    counts = np.bincount(v.astype(np.int64) - 1, minlength=parents)[:parents]
    n = counts.sum()
    srt = np.sort(counts)[::-1]
    return counts, {
        "max_share": float(srt[0] / n),
        "top10_share": float(srt[:10].sum() / n),
        "distinct": float((counts > 0).sum()),
        "mean_rank": float((v.astype(np.int64) - 1).mean() / parents),
        "null_rate": float(col.isna().mean()),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--domain", required=True)
    ap.add_argument("--scale", required=True)
    ap.add_argument("--table", required=True)
    ap.add_argument("--column", required=True)
    ap.add_argument("--parent-table", required=True)
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    raw = verify.load_schema_json(a.domain)
    tables = list(raw["tables"])
    runs = {
        "spindle": [("spindle", s) for s in range(43, 43 + a.n)],
        "shape": [("shape", s) for s in range(1042, 1042 + a.n)],
    }
    res: dict = {"args": vars(a)}
    counts: dict[str, list[np.ndarray]] = {}
    stats_by: dict[str, list[dict[str, float]]] = {}
    for label, lst in runs.items():
        counts[label], stats_by[label] = [], []
        for impl, seed in lst:
            if not verify.ensure_run(impl, a.domain, a.scale, seed, tables):
                print(f"cannot generate {impl} {seed}", file=sys.stderr)
                return 2
            d = generate.out_dir(impl, a.domain, a.scale, seed)
            col = pd.read_parquet(d / f"{a.table}.parquet", columns=[a.column])[a.column]
            parents = len(pd.read_parquet(d / f"{a.parent_table}.parquet", columns=[a.column]))
            c, s = run_stats(col, parents)
            counts[label].append(c)
            stats_by[label].append(s)
    size = min(len(c) for c in counts["spindle"] + counts["shape"])
    pooled = {k: np.sum([c[:size] for c in v], axis=0) for k, v in counts.items()}
    # merge parents into bins of ~equal pooled expectation (>= 50 per cell) for the chi-square
    both = pooled["spindle"] + pooled["shape"]
    order = np.arange(size)
    bins, acc, start = [], 0, 0
    for i in order:
        acc += both[i]
        if acc >= 100:
            bins.append((start, i + 1))
            start, acc = i + 1, 0
    if start < size and bins:
        bins[-1] = (bins[-1][0], size)
    table = np.array(
        [[p[lo:hi].sum() for lo, hi in bins] for p in (pooled["spindle"], pooled["shape"])]
    )
    chi2, p_chi, dof, _ = stats.chi2_contingency(table)
    res["pooled_chi2"] = {
        "chi2": float(chi2),
        "dof": int(dof),
        "p": float(p_chi),
        "bins": len(bins),
    }
    res["per_run"] = {}
    for key in stats_by["spindle"][0]:
        x = np.array([s[key] for s in stats_by["spindle"]])
        y = np.array([s[key] for s in stats_by["shape"]])
        if np.ptp(np.concatenate([x, y])) == 0:
            p_mw = p_ks = 1.0
        else:
            p_mw = float(stats.mannwhitneyu(x, y).pvalue)
            p_ks = float(stats.ks_2samp(x, y).pvalue)
        res["per_run"][key] = {
            "spindle_mean": float(x.mean()),
            "spindle_sd": float(x.std(ddof=1)),
            "shape_mean": float(y.mean()),
            "shape_sd": float(y.std(ddof=1)),
            "p_mannwhitney": p_mw,
            "p_ks": p_ks,
        }
    Path(a.out).write_text(json.dumps(res, indent=1) + "\n", "utf-8")
    print(json.dumps({"pooled_chi2": res["pooled_chi2"], **res["per_run"]}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
