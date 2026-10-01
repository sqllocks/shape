"""Run the pinned baseline's safe-profile mapper and validator (internal parity harness).

Runs under the *baseline* venv:
    source scripts/env.sh && "$SPINDLE_PY" spindle_safe_dump.py <input.csv|input.parquet> <out.json>

Writes {"variants": {name: safe dict}, "validator": {fixture: [[rule, path], ...]}} where the
variants are the mapper's configurations (see VARIANTS) and the validator block is its verdict
on the safe artifacts plus the fixtures in ``fixtures.py``.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[0]))
from fixtures import FIXTURES  # noqa: E402
from paths import SPINDLE_ROOT  # noqa: E402

VARIANTS = {
    "default": ({}, False),
    "k11": ({"k": 11}, False),
    "sensitive": ({"sensitive": True}, False),
    "unsafe": ({}, True),
    "no_pii_gate": ({"pii_gate": False}, False),
    "widen_bounds": ({"bounds_lo_quantile": "p0_5", "bounds_hi_quantile": "p99_5"}, False),
}


def main(src: str, out: str) -> None:
    sys.path.insert(0, str(SPINDLE_ROOT))
    import pandas as pd
    from sqllocks_spindle.inference.profiler import DataProfiler, DatasetProfile
    from sqllocks_spindle.inference.safe_profile import SafeProfile
    from sqllocks_spindle.inference.safe_validator import SafeProfileValidator

    p = Path(src)
    if p.suffix == ".parquet":
        tp = DataProfiler().profile(pd.read_parquet(p), table_name=p.stem)
    else:
        tp = DataProfiler.from_csv(p)
    dataset = DatasetProfile(tables={tp.name: tp}, relationships=[])

    variants = {}
    for name, (cfg, unsafe) in VARIANTS.items():
        variants[name] = SafeProfile.from_dataset_profile(
            dataset, config=cfg, unsafe_full_fidelity=unsafe
        ).to_safe_dict()

    validator = SafeProfileValidator()
    verdicts = {}
    with tempfile.TemporaryDirectory() as tmp:
        docs = {f"safe:{n}": v for n, v in variants.items()} | FIXTURES
        for name, doc in docs.items():
            f = Path(tmp) / "a.json"
            f.write_text(json.dumps(doc), encoding="utf-8")
            res = validator.validate_file(f)
            verdicts[name] = sorted([x.rule, x.path] for x in res.findings)
    Path(out).write_text(json.dumps({"variants": variants, "validator": verdicts}), "utf-8")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
