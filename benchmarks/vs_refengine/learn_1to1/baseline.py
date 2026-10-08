"""The baseline's ``learn`` output on D2, committed as a fixture.

Run in the *baseline* venv:

    source scripts/env.sh
    "$REFENGINE_PY" benchmarks/vs_refengine/learn_1to1/baseline.py          # write the fixture
    "$REFENGINE_PY" benchmarks/vs_refengine/learn_1to1/baseline.py --check  # fixture == baseline

The fixture (``fixtures/learn/d2_20000.json``) is the schema the pinned baseline learns from the
first 20,000 rows of D2 (``profile_1to1/datasets.py::_d2_table``), so
``tests/generation/test_learn.py`` can compare Shape's ``learn`` with it without the baseline at
test time. ``--check`` regenerates it and fails unless the committed file is equal.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "profile_1to1"))
FIXTURE = HERE.parent / "fixtures" / "learn" / "d2_20000.json"
ROWS = 20_000


def d2_csv(directory: Path) -> Path:
    """D2's first ``ROWS`` rows as a CSV file (the writer the profiling datasets use)."""
    import datasets  # type: ignore[import-not-found]
    from pyarrow import csv

    path = directory / "d2.csv"
    csv.write_csv(datasets._csv_table(datasets._d2_table(ROWS)), str(path))
    return path


def build() -> dict:
    from baseline_learn import learn

    with tempfile.TemporaryDirectory() as tmp:
        return learn(d2_csv(Path(tmp)))


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args(argv)
    doc = build()
    if a.check:
        same = json.loads(FIXTURE.read_text()) == json.loads(json.dumps(doc))
        print("fixture equals the baseline" if same else "FIXTURE DIFFERS from the baseline")
        return 0 if same else 1
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {FIXTURE}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
