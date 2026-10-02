"""Wide seed study for T-21 clause (h): per-table and per-column ``FidelityComparator`` scores of
the baseline and of the implementation over many seeds, against the same reference run.

    source scripts/env.sh && "$SPINDLE_PY" benchmarks/vs_spindle/domain_1to1/seed_study_wide.py \\
        --domain education --scale small --n 30 --out wide.json

Runs in the baseline venv. Missing runs are generated first (``generate.py``, each tool in its own
venv): the baseline at seeds 42 (the reference) and 43..42+N, the implementation at 1042..1041+N.
Every seed is scored against the same reference (the baseline's seed 42), as ``verify.py`` does,
so the two score samples are draws of the same statistic. Nothing here changes a verdict; the
output is the raw data for ``seed_study_analyze.py``.
"""

from __future__ import annotations

import argparse
import json
import sys
import warnings
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import verify  # noqa: E402
from paths import SPINDLE_ROOT  # noqa: E402

REF_SEED = 42


def _col(cf) -> dict:
    keys = (
        "score",
        "dtype_match",
        "null_rate_delta",
        "cardinality_ratio",
        "mean_delta",
        "std_ratio",
        "ks_statistic",
        "chi2_statistic",
        "chi2_pvalue",
        "value_overlap",
    )
    return {k: getattr(cf, k) for k in keys}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--domain", required=True)
    ap.add_argument("--scale", required=True)
    ap.add_argument("--n", type=int, default=30, help="seeds per tool")
    ap.add_argument("--impl", default="shape", choices=["shape", "reference_port"])
    ap.add_argument("--jobs", type=int, default=3)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    warnings.filterwarnings("ignore")
    sys.path.insert(0, str(SPINDLE_ROOT))
    from sqllocks_spindle.inference.comparator import FidelityComparator

    raw = verify.load_schema_json(args.domain)
    tables = list(raw["tables"])
    base_seeds = [REF_SEED, *range(43, 43 + args.n)]
    impl_seeds = list(range(1042, 1042 + args.n))
    runs = [("spindle", s) for s in base_seeds] + [(args.impl, s) for s in impl_seeds]
    with ThreadPoolExecutor(args.jobs) as ex:
        ok = list(
            ex.map(
                lambda r: verify.ensure_run(r[0], args.domain, args.scale, r[1], tables), runs
            )
        )
    if not all(ok):
        bad = [r for r, o in zip(runs, ok, strict=True) if not o]
        print(f"cannot generate: {bad}", file=sys.stderr)
        return 2

    def frames(impl: str, seed: int) -> dict[str, pd.DataFrame]:
        return verify.load_run(impl, args.domain, args.scale, seed, tables)[0]

    fc = FidelityComparator()
    ref = frames("spindle", REF_SEED)
    out: dict = {
        "domain": args.domain,
        "scale": args.scale,
        "impl": args.impl,
        "reference_seed": REF_SEED,
        "rows": {t: len(ref[t]) for t in tables},
        "spindle": {},
        "impl_runs": {},
    }
    for label, impl, seeds in (
        ("spindle", "spindle", base_seeds[1:]),
        ("impl_runs", args.impl, impl_seeds),
    ):
        for seed in seeds:
            rep = fc.compare(ref, frames(impl, seed))
            out[label][str(seed)] = {
                t: {"score": tf.score, "columns": {c: _col(cf) for c, cf in tf.columns.items()}}
                for t, tf in rep.tables.items()
            }
    Path(args.out).write_text(json.dumps(out) + "\n", "utf-8")
    print(f"{args.domain} {args.scale}: {args.n} seeds per tool -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
