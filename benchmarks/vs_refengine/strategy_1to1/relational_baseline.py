"""Observations of the pinned baseline's relational strategies, committed as fixtures.

Run in the *baseline* venv:

    source scripts/env.sh
    "$REFENGINE_PY" benchmarks/vs_refengine/strategy_1to1/relational_baseline.py          # write
    "$REFENGINE_PY" benchmarks/vs_refengine/strategy_1to1/relational_baseline.py --check  # compare

For every case of ``relational_cases.py`` it generates the multi-table schema at the fixed
baseline seeds 43, 44, 45 and 46 (T-21), and stores the fingerprint of each observation and the
exact invariants, so ``tests/generation/test_strategies_p404d.py`` needs no baseline at test
time. ``--check`` regenerates and fails unless the committed fixtures equal what the baseline
produces now.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import _refpkg  # noqa: E402
import fingerprint  # noqa: E402
import relational_cases as cases  # noqa: E402
from paths import REFENGINE_ROOT  # noqa: E402

FIXTURES = HERE.parent / "fixtures" / "strategies"
BASELINE_SEEDS = (43, 44, 45, 46)


def tables_of(result: Any) -> dict[str, dict[str, list[Any]]]:
    return {
        name: {col: result[name][col].tolist() for col in result[name].columns}
        for name in result.table_names
    }


def build(only: str | None = None) -> dict[str, dict[str, Any]]:
    sys.path.insert(0, str(REFENGINE_ROOT))
    RefEngine = getattr(_refpkg.mod(""), _refpkg.ENGINE_CLASS)

    refengine = RefEngine()
    out: dict[str, dict[str, Any]] = {}
    for case_id, case in cases.CASES.items():
        if only and case["strategy"] != only:
            continue
        observed: dict[str, list[Any]] = {}
        facts = []
        for seed in BASELINE_SEEDS:
            tables = tables_of(refengine.generate(schema=cases.schema_for(case), seed=seed))
            for name, values in cases.observe(case, tables).items():
                observed.setdefault(name, []).append(fingerprint.fingerprint(values))
            facts.append(cases.invariants(case, tables))
        entry = {"observations": observed, "invariants": facts}
        out.setdefault(case["strategy"], {"cases": {}})["cases"][case_id] = entry
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
    commit = commit_of(REFENGINE_ROOT)
    bad = 0
    for strategy, doc in built.items():
        doc = {"baseline_commit": commit, "seeds": list(BASELINE_SEEDS), **doc}
        path = FIXTURES / f"{strategy}.json"
        text = json.dumps(doc, indent=1, sort_keys=True) + "\n"
        if a.check:
            same = path.exists() and json.loads(path.read_text()) == json.loads(text)
            print(f"{strategy:24s} {'match' if same else 'DIFFERS'}")
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
