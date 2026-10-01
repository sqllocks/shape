"""Record (or check) the baseline's tier outputs for ``golden_data.py``.

    "$SPINDLE_PY" benchmarks/vs_spindle/fidelity_tiers_1to1/golden.py [--check]

Writes ``fixtures/expected_tiers.json``; ``--check`` recomputes it and exits 1 when the committed
file is not within the harness's own comparison rules of what the baseline gives now (the AUC and
mixture fields and the classifier's importances may move within 0.02 between runs, because the
baseline orders the classifier's features by Python ``set`` iteration; everything else must be
equal). The baseline sees the flag column under its own name. Run with ``$SPINDLE_PY``; the
checkout is only read.
"""

from __future__ import annotations

import argparse
import json
import sys
import warnings
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
FIXTURE = HERE / "fixtures" / "expected_tiers.json"
SEED = 7
SMALL_ROWS = 5000
BASELINE_FLAG = "_spindle_is_anomaly"


def compute() -> dict[str, Any]:
    warnings.simplefilter("ignore")
    from baseline_tiers import tiers_for
    from golden_data import tables

    real, synth = tables()
    out: dict[str, Any] = {"tables": {}}
    for name, r in real.items():
        s = synth[name].to_pandas()
        s = s.rename(columns={"_shape_is_anomaly": BASELINE_FLAG})
        out["tables"][name] = tiers_for(
            name, r.to_pandas(), s, tier1=True, small_rows=SMALL_ROWS, seed=SEED
        )
        out["tables"][name].pop("tier1_s", None)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args(argv)
    got = compute()
    if a.check:
        from tiers_common import compare

        d = compare(got, json.loads(FIXTURE.read_text()))
        for line in d.mismatches[:20]:
            print(line)
        print(
            "matches the baseline's tiers"
            if d.ok
            else f"DIFFERS from the baseline's tiers ({len(d.mismatches)} fields)"
        )
        return 0 if d.ok else 1
    FIXTURE.parent.mkdir(exist_ok=True)
    FIXTURE.write_text(json.dumps(got, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {FIXTURE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
