"""Two-sample tests on the output of ``seed_study_wide.py`` (stdlib + scipy, any venv).

    "$SPINDLE_PY" benchmarks/vs_spindle/domain_1to1/seed_study_analyze.py wide_*.json --out analysis.json

For every table (and every column of it) the baseline's N scores and the implementation's N scores
are compared with a two-sided Mann-Whitney U test and a two-sample KS test; the p-values are
Bonferroni-corrected over every test run in the invocation (``--alpha``, default 0.05). It also
reports where the implementation's seed-1042 score falls in the implementation's own distribution
(rank, empirical percentile) and in the baseline's, and the share of each tool's seeds below the
verifier's floor (min over baseline seeds 43-46, minus 0.5). Nothing here is a verdict of the
verifier.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from scipy import stats

FLOOR_SEEDS = ("43", "44", "45", "46")


def pct_rank(x: float, sample: np.ndarray) -> float:
    """Share of the sample at or below x."""
    return float(np.mean(sample <= x))


def analyse(study: dict) -> list[dict]:
    rows = []
    sp, im = study["spindle"], study["impl_runs"]
    for t in study["rows"]:
        tab_sp = np.array([sp[s][t]["score"] for s in sp])
        tab_im = np.array([im[s][t]["score"] for s in im])
        floor = min(sp[s][t]["score"] for s in FLOOR_SEEDS) - 0.5
        s1042 = im["1042"][t]["score"]
        entries = [("<table>", tab_sp, tab_im, s1042)]
        for c in sp["43"][t]["columns"]:
            a = np.array([sp[s][t]["columns"][c]["score"] for s in sp])
            b = np.array([im[s][t]["columns"][c]["score"] for s in im])
            entries.append((c, a, b, im["1042"][t]["columns"][c]["score"]))
        for c, a, b, one in entries:
            if np.ptp(np.concatenate([a, b])) == 0:
                p_mw = p_ks = 1.0
            else:
                p_mw = float(stats.mannwhitneyu(a, b, alternative="two-sided").pvalue)
                p_ks = float(stats.ks_2samp(a, b).pvalue)
            rows.append(
                {
                    "domain": study["domain"],
                    "scale": study["scale"],
                    "table": t,
                    "column": c,
                    "rows": study["rows"][t],
                    "n_spindle": len(a),
                    "n_impl": len(b),
                    "spindle_mean": float(a.mean()),
                    "spindle_sd": float(a.std(ddof=1)),
                    "spindle_min": float(a.min()),
                    "impl_mean": float(b.mean()),
                    "impl_sd": float(b.std(ddof=1)),
                    "impl_min": float(b.min()),
                    "impl_seed1042": float(one),
                    "impl_pct_1042_in_impl": pct_rank(one, b),
                    "impl_pct_1042_in_spindle": pct_rank(one, a),
                    "p_mannwhitney": p_mw,
                    "p_ks": p_ks,
                    "floor": floor if c == "<table>" else None,
                    "frac_spindle_below_floor": float(np.mean(a < floor))
                    if c == "<table>"
                    else None,
                    "frac_impl_below_floor": float(np.mean(b < floor)) if c == "<table>" else None,
                }
            )
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("studies", nargs="+", type=Path)
    ap.add_argument("--alpha", type=float, default=0.05)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    rows = []
    for p in args.studies:
        rows += analyse(json.loads(p.read_text("utf-8")))
    m_tab = sum(r["column"] == "<table>" for r in rows)
    m = {"<table>": m_tab, "column": len(rows) - m_tab}
    for r in rows:
        k = m["<table>" if r["column"] == "<table>" else "column"]
        r["bonferroni_m"] = k
        r["p_mw_bonf"] = min(1.0, r["p_mannwhitney"] * k)
        r["p_ks_bonf"] = min(1.0, r["p_ks"] * k)
        r["significant"] = bool(r["p_mw_bonf"] < args.alpha or r["p_ks_bonf"] < args.alpha)
    args.out.write_text(
        json.dumps({"alpha": args.alpha, "tests": m, "rows": rows}, indent=1) + "\n", "utf-8"
    )
    print(f"tests {m}: Bonferroni within the table level and within the column level, alpha {args.alpha}")
    for r in rows:
        if r["column"] == "<table>":
            print(
                f"{r['domain']:14s} {r['scale']:6s} {r['table']:22s} sp {r['spindle_mean']:6.2f}"
                f"+-{r['spindle_sd']:4.2f}  im {r['impl_mean']:6.2f}+-{r['impl_sd']:4.2f}  "
                f"1042 {r['impl_seed1042']:6.2f} (pct {r['impl_pct_1042_in_impl']:.2f})  "
                f"p_mw {r['p_mannwhitney']:.3g} p_ks {r['p_ks']:.3g}"
                f"{'  SIGNIFICANT' if r['significant'] else ''}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
