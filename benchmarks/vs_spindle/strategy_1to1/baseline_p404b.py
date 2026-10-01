"""Fingerprints of the pinned baseline's ``faker``, ``native``, ``formula``, ``derived`` and
``computed`` strategies, committed as fixtures (P4-04b).

Run in the *baseline* venv:

    source scripts/env.sh
    P=benchmarks/vs_spindle/strategy_1to1/baseline_p404b.py
    "$SPINDLE_PY" $P          # write the fixtures
    "$SPINDLE_PY" $P --check  # fixtures == baseline

Each case of ``cases_p404b.py`` is generated at the fixed baseline seeds 43-46 (T-21). The fixture
keeps, per seed, the fingerprint of the measured series (see ``cases_p404b``), the component
fingerprints of pooled text (``parts.py``) and the sha256 of every pool the baseline holds, so the
test can prove Shape's pools are the same.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import cases_p404b  # noqa: E402
import export_pools  # noqa: E402
import fingerprint  # noqa: E402
import parts  # noqa: E402
from baseline import BASELINE_SEEDS, commit_of  # noqa: E402
from paths import SPINDLE_ROOT  # noqa: E402

FIXTURES = HERE.parent / "fixtures" / "strategies"
FIXTURE_STRATEGIES = ("native", "faker", "formula", "derived", "computed")


def pool_digests(pools: dict[str, tuple[str, ...]]) -> dict[str, str]:
    return {
        name: hashlib.sha256(("".join(f"{e}\n" for e in entries)).encode("utf-8")).hexdigest()
        for name, entries in sorted(pools.items())
    }


def build(only: str | None = None) -> dict[str, dict[str, Any]]:
    sys.path.insert(0, str(SPINDLE_ROOT))
    from sqllocks_spindle import Spindle

    spindle = Spindle()
    pools = export_pools.baseline_pools()
    out: dict[str, dict[str, Any]] = {}
    for case_id, case in cases_p404b.CASES.items():
        strategy = case["strategy"]
        if only and strategy != only:
            continue
        runs = []
        for seed in BASELINE_SEEDS:
            result = spindle.generate(schema=cases_p404b.schema_for(case, "baseline"), seed=seed)
            tables = {
                n: {c: list(df[c].tolist()) for c in df.columns} for n, df in result.tables.items()
            }
            series = parts.measure(case, tables)
            run: dict[str, Any] = {"fingerprint": fingerprint.fingerprint(series)}
            if case["regex"]:
                rx = re.compile(case["regex"])
                present = [v for v in series if isinstance(v, str)]
                run["regex_match_rate"] = sum(1 for v in present if rx.fullmatch(v)) / max(
                    1, len(present)
                )
            if case["parts"]:
                run["parts"] = parts.parts_fingerprint(series, case["parts"], pools)
            runs.append(run)
        out.setdefault(strategy, {"cases": {}})["cases"][case_id] = {"runs": runs}
    for doc in out.values():
        doc["pool_sha256"] = pool_digests(pools)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true", help="compare the fixtures with the baseline")
    ap.add_argument("--strategy", choices=FIXTURE_STRATEGIES, help="only this strategy")
    a = ap.parse_args(argv)
    built = build(a.strategy)
    commit = commit_of(SPINDLE_ROOT)
    bad = 0
    for strategy, doc in built.items():
        doc = {
            "baseline_commit": commit,
            "seeds": list(BASELINE_SEEDS),
            "rows": cases_p404b.ROWS,
            **doc,
        }
        path = FIXTURES / f"{strategy}.json"
        text = json.dumps(doc, indent=1, sort_keys=True) + "\n"
        if a.check:
            same = path.exists() and json.loads(path.read_text()) == json.loads(text)
            print(f"{strategy:10s} {'match' if same else 'DIFFERS'}")
            bad += 0 if same else 1
        else:
            FIXTURES.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
            print(f"wrote {path.relative_to(HERE.parents[2])} ({len(doc['cases'])} cases)")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
