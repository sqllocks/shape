"""Clause (f) rates over many seeds of each tool (P6-01e-seed).

    source scripts/env.sh && "$REFENGINE_PY" benchmarks/vs_refengine/domain_1to1/rate_check.py \\
        --domain composite_enterprise --scale small --n 30 --out rates.json

``verify.py`` clause (f) takes every strategy-semantics rate of Shape's seed 1042 and fails it when
it is below the minimum of the baseline's seeds 42-46 minus 0.005. This computes the same rates
(``verify.semantic_rates``) for baseline seeds 42..42+n and Shape seeds 1042..1041+n, and reports
per rate: mean and sd of each tool, two-sample Mann-Whitney and KS, the verifier's floor
(seeds 42-46), and the share of fresh baseline seeds (47 onward, not among those that set the floor)
and of Shape seeds under that floor. Nothing here is a clause of ``verify.py``. Each run is deleted
after its rates are taken (``--keep`` to keep).
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

import numpy as np
from scipy import stats

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import generate  # noqa: E402
import verify  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--domain", required=True)
    ap.add_argument("--scale", required=True)
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    raw = verify.load_schema_json(a.domain)
    tables = list(raw["tables"])
    runs = {
        "refengine": [("refengine", s) for s in range(42, 43 + a.n)],
        "shape": [("shape", s) for s in range(1042, 1042 + a.n)],
    }
    rates: dict[str, dict[int, dict[str, float]]] = {"refengine": {}, "shape": {}}
    for label, lst in runs.items():
        for impl, seed in lst:
            if not verify.ensure_run(impl, a.domain, a.scale, seed, tables):
                print(f"cannot generate {impl} {seed}", file=sys.stderr)
                return 2
            frames = verify.load_run(impl, a.domain, a.scale, seed, tables)[0]
            rates[label][seed] = verify.semantic_rates(frames, raw)
            if not a.keep and not (impl == "refengine" and seed == 42):
                shutil.rmtree(generate.out_dir(impl, a.domain, a.scale, seed), ignore_errors=True)
    out: dict = {"domain": a.domain, "scale": a.scale, "rates": {}}
    for k in rates["refengine"][42]:
        floor = min(rates["refengine"][s][k] for s in range(42, 47)) - 0.005
        base = np.array([rates["refengine"][s][k] for s in range(47, 43 + a.n)])
        shp = np.array([rates["shape"][s][k] for s in rates["shape"]])
        if np.ptp(np.concatenate([base, shp])) == 0:
            p_mw = p_ks = 1.0
        else:
            p_mw = float(stats.mannwhitneyu(base, shp).pvalue)
            p_ks = float(stats.ks_2samp(base, shp).pvalue)
        out["rates"][k] = {
            "floor": floor,
            "baseline_mean": float(base.mean()),
            "baseline_sd": float(base.std(ddof=1)),
            "shape_mean": float(shp.mean()),
            "shape_sd": float(shp.std(ddof=1)),
            "shape_1042": float(rates["shape"][1042][k]),
            "frac_fresh_baseline_below_floor": float(np.mean(base < floor)),
            "frac_shape_below_floor": float(np.mean(shp < floor)),
            "p_mannwhitney": p_mw,
            "p_ks": p_ks,
        }
    Path(a.out).write_text(json.dumps(out, indent=1) + "\n", "utf-8")
    for k, v in out["rates"].items():
        print(
            f"{k[:70]:70s} bl {v['baseline_mean']:.4f}+-{v['baseline_sd']:.4f} "
            f"sh {v['shape_mean']:.4f}+-{v['shape_sd']:.4f} 1042 {v['shape_1042']:.4f} "
            f"floor {v['floor']:.4f} below bl/sh {v['frac_fresh_baseline_below_floor']:.2f}/"
            f"{v['frac_shape_below_floor']:.2f} p_mw {v['p_mannwhitney']:.2g}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
