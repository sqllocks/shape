"""Shape's ``learn`` against the baseline's on the same data (internal harness).

    source scripts/env.sh
    "$SHAPE_VENV/bin/python" benchmarks/vs_spindle/learn_1to1/verify.py [DATASET ...]

Datasets: ``d2`` (the 1M-row profiling dataset, the acceptance case), ``mt`` (three related
tables) and ``d1``; default ``d2 mt``. For each, the baseline's builder runs in the baseline venv
(``baseline_learn.py``), Shape's ``shape learn`` runs as the product command, and ``compare.py``
lists every difference: unexplained ones fail the run (exit 1); the deliberate ones are printed with
their reason. Writes the two schemas under ``$BENCH_OUT_DIR/learn_1to1/``.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
from compare import compare  # noqa: E402
from paths import BENCH_OUT_DIR, PROFILE_DATA_DIR, SHAPE_PY, SPINDLE_PY  # noqa: E402

DATASETS = {"d1": "d1.csv", "d2": "d2.csv", "d4": "d4.csv", "mt": "mt"}


def run_both(name: str, out_dir: Path) -> tuple[dict, dict]:
    src = PROFILE_DATA_DIR / DATASETS[name]
    out_dir.mkdir(parents=True, exist_ok=True)
    base_out, shape_out = out_dir / f"{name}.baseline.json", out_dir / f"{name}.shape.json"
    subprocess.run(
        [str(SPINDLE_PY), str(HERE / "baseline_learn.py"), str(src), "-o", str(base_out)],
        check=True,
    )
    subprocess.run(
        [str(SHAPE_PY.with_name("shape")), "learn", str(src), "-o", str(shape_out)],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    return json.loads(shape_out.read_text()), json.loads(base_out.read_text())


def main(argv: list[str]) -> int:
    names = argv or ["d2", "mt"]
    failed = 0
    for name in names:
        shape_doc, base_doc = run_both(name, BENCH_OUT_DIR / "learn_1to1")
        bad, explained = compare(shape_doc, base_doc)
        cols = sum(len(t["columns"]) for t in base_doc["tables"].values())
        print(
            f"{name}: {cols} baseline columns, {len(explained)} deliberate differences, "
            f"{len(bad)} unexplained"
        )
        for e in explained:
            print(f"  allowed {e['column']} [{e['rule']}]: {e['reason']}")
        for line in bad:
            print(f"  FAIL {line}")
        failed += bool(bad)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
