"""Record (or check) the baseline comparator's scores for ``golden_data.py``.

    "$SPINDLE_PY" benchmarks/vs_spindle/fidelity_1to1/golden.py [--check]

Writes ``fixtures/expected_scores.json``; ``--check`` recomputes it and exits 1 when the file is
not what the baseline's comparator gives now (so the committed fixture can never drift from it).
Run with ``$SPINDLE_PY``; the checkout is only read.
"""

from __future__ import annotations

import argparse
import json
import sys
import warnings
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
FIXTURE = HERE / "fixtures" / "expected_scores.json"


def compute() -> dict:
    warnings.simplefilter("ignore")
    from golden_data import tables
    from sqllocks_spindle.inference.comparator import FidelityComparator

    real, synth = tables()
    rep = FidelityComparator().compare(
        {k: v.to_pandas() for k, v in real.items()}, {k: v.to_pandas() for k, v in synth.items()}
    )
    return {
        "overall_score": rep.overall_score,
        "tables": {
            t: {"score": tf.score, "columns": {c: cf.score for c, cf in tf.columns.items()}}
            for t, tf in rep.tables.items()
        },
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args(argv)
    got = compute()
    if a.check:
        want = json.loads(FIXTURE.read_text())
        same = json.dumps(got, sort_keys=True) == json.dumps(want, sort_keys=True)
        print("matches the baseline comparator" if same else "DIFFERS from the baseline comparator")
        return 0 if same else 1
    FIXTURE.write_text(json.dumps(got, indent=1, sort_keys=True) + "\n")
    print(f"wrote {FIXTURE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
