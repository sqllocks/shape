"""Fingerprints of the pinned baseline's strategies, committed as fixtures.

Run in the *baseline* venv:

    source scripts/env.sh
    "$SPINDLE_PY" benchmarks/vs_spindle/strategy_1to1/baseline.py          # write the fixtures
    "$SPINDLE_PY" benchmarks/vs_spindle/strategy_1to1/baseline.py --check  # fixtures == baseline

For every case in ``cases.py`` it generates the single-table schema at the fixed baseline seeds
43, 44, 45 and 46 (T-21) and stores the fingerprint of the column under test, so the per-strategy
tests (``tests/generation/test_strategies_p404a.py``) need no baseline at test time. ``--check``
regenerates and fails unless the committed fixtures equal what the baseline produces now.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import cases  # noqa: E402
import fingerprint  # noqa: E402
from paths import SPINDLE_ROOT  # noqa: E402

FIXTURES = HERE.parent / "fixtures" / "strategies"
BASELINE_SEEDS = (43, 44, 45, 46)


def column_values(spindle: Any, case: dict[str, Any], seed: int) -> list[Any]:
    result = spindle.generate(schema=cases.schema_for(case), seed=seed)
    return list(result[cases.TABLE][cases.TARGET].tolist())


def build(only: str | None = None) -> dict[str, dict[str, Any]]:
    sys.path.insert(0, str(SPINDLE_ROOT))
    from sqllocks_spindle import Spindle

    spindle = Spindle()
    out: dict[str, dict[str, Any]] = {}
    for case_id, case in cases.CASES.items():
        strategy = case["strategy"]
        if only and strategy != only:
            continue
        fps = []
        for seed in BASELINE_SEEDS:
            values = column_values(spindle, case, seed)
            fp = fingerprint.fingerprint(values)
            if case["regex"]:
                rx = re.compile(case["regex"])
                fp["regex_match_rate"] = sum(
                    1 for v in values if isinstance(v, str) and rx.fullmatch(v)
                ) / max(1, fp["n"] - fp["null_count"])
            fps.append(fp)
        out.setdefault(strategy, {"cases": {}})["cases"][case_id] = {"fingerprints": fps}
    return out


def commit_of(root: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true", help="compare the fixtures with the baseline")
    ap.add_argument("--strategy", help="only this strategy")
    a = ap.parse_args(argv)
    built = build(a.strategy)
    commit = commit_of(SPINDLE_ROOT)
    bad = 0
    for strategy, doc in built.items():
        doc = {"baseline_commit": commit, "seeds": list(BASELINE_SEEDS), "rows": cases.ROWS, **doc}
        path = FIXTURES / f"{strategy}.json"
        text = json.dumps(doc, indent=1, sort_keys=True) + "\n"
        if a.check:
            same = path.exists() and json.loads(path.read_text()) == json.loads(text)
            print(f"{strategy:15s} {'match' if same else 'DIFFERS'}")
            bad += 0 if same else 1
        else:
            FIXTURES.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
            print(
                f"wrote {path.relative_to(HERE.parent.parent.parent)} ({len(doc['cases'])} cases)"
            )
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
