"""Clause (h) floor-miss rates per cell, from ``seed_study_wide*.py`` output (P6-01e-seed).

    "$REFENGINE_PY" benchmarks/vs_refengine/domain_1to1/seed_study_floor_misses.py \\
        wide_*.json --out misses.json

The verifier's floor of a table is the minimum of the baseline's seeds 43-46 minus 0.5. For each
cell this counts, per seed, the tables whose score is under their own floor: for the fresh
baseline seeds (47 onward, which did not set a floor) and for the Shape seeds (1042 onward). It
reports both distributions, a Mann-Whitney test on the two lists of counts, where Shape's seed 1042
falls, and the share of seeds with no table under its floor (the seeds that would pass clause (h)
whole). Nothing here is a clause of ``verify.py``.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from scipy import stats

FLOOR_SEEDS = ("43", "44", "45", "46")


def cell(study: dict) -> dict:
    sp, im = study["refengine"], study["impl_runs"]
    tables = list(study["rows"])
    floors = {t: min(sp[s][t]["score"] for s in FLOOR_SEEDS) - 0.5 for t in tables}
    fresh = [s for s in sp if int(s) >= 47]

    def misses(run: dict) -> list[str]:
        return [t for t in tables if run[t]["score"] < floors[t]]

    bl = {s: misses(sp[s]) for s in fresh}
    sh = {s: misses(im[s]) for s in im}
    nb = np.array([len(v) for v in bl.values()])
    ns = np.array([len(v) for v in sh.values()])
    p = float(stats.mannwhitneyu(nb, ns).pvalue) if np.ptp(np.concatenate([nb, ns])) else 1.0
    return {
        "domain": study["domain"],
        "scale": study["scale"],
        "tables": len(tables),
        "n_fresh_baseline": len(nb),
        "n_shape": len(ns),
        "baseline_misses_mean": float(nb.mean()),
        "baseline_misses_min": int(nb.min()),
        "baseline_misses_max": int(nb.max()),
        "shape_misses_mean": float(ns.mean()),
        "shape_misses_min": int(ns.min()),
        "shape_misses_max": int(ns.max()),
        "shape_1042_misses": len(sh["1042"]),
        "shape_1042_tables": sh["1042"],
        "shape_1042_rank_share_at_or_above": float(np.mean(ns >= len(sh["1042"]))),
        "p_mannwhitney_counts": p,
        "baseline_seeds_with_no_miss": float(np.mean(nb == 0)),
        "shape_seeds_with_no_miss": float(np.mean(ns == 0)),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("studies", nargs="+", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    rows = [cell(json.loads(p.read_text("utf-8"))) for p in a.studies]
    a.out.write_text(json.dumps(rows, indent=1) + "\n", "utf-8")
    for r in rows:
        lo, hi = r["baseline_misses_min"], r["baseline_misses_max"]
        bl = f"{r['baseline_misses_mean']:5.2f} ({lo}-{hi})"
        sh = f"{r['shape_misses_mean']:5.2f} ({r['shape_misses_min']}-{r['shape_misses_max']})"
        at = r["shape_1042_rank_share_at_or_above"]
        print(
            f"{r['domain'][10:]:40s} {r['scale']:6s} tables {r['tables']:3d} | fresh baseline {bl}"
            f" | shape {sh} | 1042: {r['shape_1042_misses']} (share of seeds >= {at:.2f})"
            f" | MW p {r['p_mannwhitney_counts']:.2f}"
            f" | no-miss seeds bl {r['baseline_seeds_with_no_miss']:.2f}"
            f" sh {r['shape_seeds_with_no_miss']:.2f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
