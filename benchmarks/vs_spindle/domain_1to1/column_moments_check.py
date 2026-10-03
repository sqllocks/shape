"""Per-run moments of chosen numeric columns over many seeds of each tool (P6-01e-seed).

    source scripts/env.sh && "$SPINDLE_PY" \\
        benchmarks/vs_spindle/domain_1to1/column_moments_check.py \\
        --domain composite_digital_commerce --scale medium --n 30 --out moments.json \\
        retail_order_line.unit_price retail_order_line.line_total retail_order.order_total

Reads the runs ``generate.py`` writes (missing ones are generated, each tool in its own venv) and
deleted after use, for the baseline seeds 43..42+n and the Shape seeds 1042..1041+n. For each
``table.column`` it takes, per run, the mean, the median, the 99th percentile and the sum, and
compares the two tools' lists with a two-sample Mann-Whitney and KS test. A distribution that is
the same in both tools gives the same distribution of these per-run values. Nothing here is a
clause of ``verify.py``.
"""

from __future__ import annotations

import argparse
import json
import shutil
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


def moments(v: pd.Series) -> dict[str, float]:
    x = pd.to_numeric(v, errors="coerce").dropna().to_numpy(dtype=float)
    return {
        "mean": float(x.mean()),
        "median": float(np.median(x)),
        "p99": float(np.percentile(x, 99)),
        "sum": float(x.sum()),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("columns", nargs="+", help="table.column")
    ap.add_argument("--domain", required=True)
    ap.add_argument("--scale", required=True)
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    tables = list(verify.load_schema_json(a.domain)["tables"])
    runs = {
        "spindle": [("spindle", s) for s in range(43, 43 + a.n)],
        "shape": [("shape", s) for s in range(1042, 1042 + a.n)],
    }
    per: dict[str, dict[str, list[dict[str, float]]]] = {
        k: {c: [] for c in a.columns} for k in runs
    }
    for label, lst in runs.items():
        for impl, seed in lst:
            if not verify.ensure_run(impl, a.domain, a.scale, seed, tables):
                print(f"cannot generate {impl} {seed}", file=sys.stderr)
                return 2
            d = generate.out_dir(impl, a.domain, a.scale, seed)
            for spec in a.columns:
                t, c = spec.split(".")
                per[label][spec].append(
                    moments(pd.read_parquet(d / f"{t}.parquet", columns=[c])[c])
                )
            shutil.rmtree(d, ignore_errors=True)
    out: dict = {"args": vars(a), "columns": {}}
    for spec in a.columns:
        out["columns"][spec] = {}
        for m in ("mean", "median", "p99", "sum"):
            x = np.array([r[m] for r in per["spindle"][spec]])
            y = np.array([r[m] for r in per["shape"][spec]])
            out["columns"][spec][m] = {
                "baseline_mean": float(x.mean()),
                "baseline_sd": float(x.std(ddof=1)),
                "shape_mean": float(y.mean()),
                "shape_sd": float(y.std(ddof=1)),
                "p_mannwhitney": float(stats.mannwhitneyu(x, y).pvalue),
                "p_ks": float(stats.ks_2samp(x, y).pvalue),
                "p_levene": float(stats.levene(x, y).pvalue),
            }
            r = out["columns"][spec][m]
            print(
                f"{spec:34s} {m:6s} bl {r['baseline_mean']:12.4f}+-{r['baseline_sd']:9.4f} "
                f"sh {r['shape_mean']:12.4f}+-{r['shape_sd']:9.4f} MW {r['p_mannwhitney']:.3f} "
                f"KS {r['p_ks']:.3f} Levene {r['p_levene']:.3f}"
            )
    Path(a.out).write_text(json.dumps(out, indent=1) + "\n", "utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
