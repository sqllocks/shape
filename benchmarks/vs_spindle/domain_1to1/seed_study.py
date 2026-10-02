"""Seed study for T-21 clause (h): the baseline's per-table ``FidelityComparator`` score of the
implementation at several seeds against the floor (min over the baseline seeds 43-46, minus 0.5).

    source scripts/env.sh && "$SPINDLE_PY" benchmarks/vs_spindle/domain_1to1/seed_study.py \\
        --domain education --scale small --out study.json

Runs in the baseline venv. Missing runs are generated first (``generate.py``, each tool in its own
venv): the baseline at seeds 42-46, the implementation at 1042-1049. It changes no verdict: the
verifier's seed set is fixed (1042 against 43-46); this only shows whether a clause-(h) miss at
seed 1042 is the chance spread of the comparator or a defect (T-21 section 3.2 of the plan).
"""

from __future__ import annotations

import argparse
import json
import sys
import warnings
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import generate  # noqa: E402
import verify  # noqa: E402
from paths import SPINDLE_ROOT  # noqa: E402

REF_SEED, BASELINE_SEEDS = 42, (43, 44, 45, 46)
IMPL_SEEDS = tuple(range(1042, 1050))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--domain", required=True)
    ap.add_argument("--scale", required=True)
    ap.add_argument("--impl", default="shape", choices=["shape", "reference_port"])
    ap.add_argument("--out", required=True, help="report JSON")
    args = ap.parse_args(argv)
    warnings.filterwarnings("ignore")
    sys.path.insert(0, str(SPINDLE_ROOT))
    from sqllocks_spindle.inference.comparator import FidelityComparator

    raw = verify.load_schema_json(args.domain)
    tables = list(raw["tables"])
    runs = [("spindle", s) for s in (REF_SEED, *BASELINE_SEEDS)] + [
        (args.impl, s) for s in IMPL_SEEDS
    ]
    for impl, seed in runs:
        if not verify.ensure_run(impl, args.domain, args.scale, seed, tables):
            print(f"cannot generate {impl} seed {seed}", file=sys.stderr)
            return 2

    def frames(impl: str, seed: int) -> dict[str, pd.DataFrame]:
        return verify.load_run(impl, args.domain, args.scale, seed, tables)[0]

    fc = FidelityComparator()
    ref = frames("spindle", REF_SEED)
    base = {s: fc.compare(ref, frames("spindle", s)) for s in BASELINE_SEEDS}
    floor = {t: min(base[s].tables[t].score for s in BASELINE_SEEDS) - 0.5 for t in tables}
    study: dict = {
        "domain": args.domain,
        "scale": args.scale,
        "impl": args.impl,
        "floor": floor,
        "baseline": {
            str(s): {t: base[s].tables[t].score for t in tables} for s in BASELINE_SEEDS
        },
        "impl_seeds": {},
    }
    for seed in IMPL_SEEDS:
        rep = fc.compare(ref, frames(args.impl, seed))
        scores = {t: rep.tables[t].score for t in tables}
        study["impl_seeds"][str(seed)] = {
            "scores": scores,
            "below_floor": sorted(t for t in tables if scores[t] < floor[t]),
            "shortfall": {t: floor[t] - scores[t] for t in tables if scores[t] < floor[t]},
        }
    Path(args.out).write_text(json.dumps(study, indent=1) + "\n", "utf-8")
    for seed, rec in study["impl_seeds"].items():
        print(seed, "below floor:", rec["below_floor"] or "none")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
