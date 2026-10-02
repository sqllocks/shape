"""P6-04 acceptance: parity of the ``shape-simulation`` simulators against the pinned baseline
(T-21 applied to simulators), one case module per simulator.

    source scripts/env.sh && python benchmarks/vs_spindle/simulation_1to1/verify.py [--quick]
        [--only NAME ...] [--no-controls | --controls-only]

Runs in the Shape venv. The baseline runs in its own venv (``baseline_worker.py``) at the fixed
seeds 42 (reference) and 43-46 (its own spread); Shape runs at 1042 (the set has no option, and a
verdict from another set counts for nothing). Each case module (``case_<name>.py``) defines its
configurations, how to run Shape, what to compare (``harness.py`` holds the rules and their
tolerances, derived from T-21), a negative control per case (a deliberate perturbation of
Shape's run that the comparison must catch) and the probes of the allow-list (``names.ALLOWED``:
baseline defects that Shape fixes, each shown in the baseline and absent in Shape).

Exit codes: 0 every check passed, every control was detected and every probe passed; 1 a
check failed, a control went undetected or a probe failed; 2 the baseline or an input is
missing, or a command failed.
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import harness  # noqa: E402
import names  # noqa: E402
from paths import BENCH_OUT_DIR  # noqa: E402


def discover() -> dict[str, str]:
    """``case_<name>.py`` modules next to this file, by name."""
    return {p.stem[len("case_") :]: p.stem for p in sorted(HERE.glob("case_*.py"))}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--quick", action="store_true", help="smaller inputs and populations")
    ap.add_argument("--only", nargs="+", metavar="NAME", help="run these cases only")
    ap.add_argument("--no-controls", action="store_true", help="skip the negative controls")
    ap.add_argument("--controls-only", action="store_true", help="run only the negative controls")
    a = ap.parse_args(argv)
    cases = discover()
    chosen = a.only or list(cases)
    unknown = [c for c in chosen if c not in cases]
    if unknown:
        print(f"unknown case {unknown}; cases: {', '.join(cases)}", file=sys.stderr)
        return 2
    ctx = harness.Context(quick=a.quick, only_controls=a.controls_only, skip_controls=a.no_controls)
    t0 = time.time()
    failed = 0
    results: dict[str, object] = {
        "seeds": {"reference": 42, "baseline": [43, 44, 45, 46], "shape": 1042}
    }
    covered: set[str] = set()
    for name in chosen:
        module = importlib.import_module(cases[name])
        try:
            reports, controls, probes = harness.run_case(module, ctx)
        except harness.HarnessError as exc:
            print(f"ERROR {name}: {exc}", file=sys.stderr)
            return 2
        print(f"\n=== {name} ===")
        for rep in reports:
            status = "PASS" if not rep.failed else "FAIL"
            print(
                f"{status} {rep.label}: "
                f"{len(rep.checks) - len(rep.failed)}/{len(rep.checks)} checks"
            )
            for c in rep.failed:
                print(f"    - {c.name}: {json.dumps(harness._jsonable(c.detail))[:400]}")
            failed += bool(rep.failed)
        for c in controls:
            print(
                f"{'PASS' if c['detected'] else 'FAIL'} negative control {c['control']}: "
                f"{'detected' if c['detected'] else 'NOT DETECTED'} "
                f"({len(c['failed_checks'])}+ checks failed)"
            )
            failed += not c["detected"]
        for pr in probes:
            allow_id = pr.label.split()[0]
            if allow_id not in names.ALLOWED:
                print(f"FAIL probe {pr.label}: not an allow-list entry")
                failed += 1
                continue
            covered.add(allow_id)
            print(
                f"{'PASS' if not pr.failed else 'FAIL'} allow-list {pr.label}: "
                f"{len(pr.checks) - len(pr.failed)}/{len(pr.checks)}"
            )
            for c in pr.failed:
                print(f"    - {c.name}: {json.dumps(harness._jsonable(c.detail))[:300]}")
            failed += bool(pr.failed)
        results[name] = {
            "reports": [r.as_dict() for r in reports],
            "controls": controls,
            "probes": [p.as_dict() for p in probes],
        }
    out = BENCH_OUT_DIR / "simulation_1to1"
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps(results, indent=2, default=str))
    print(f"\nallow-list entries exercised: {', '.join(sorted(covered)) or 'none'}")
    print(f"{'FAILED' if failed else 'OK'}: {failed} failure(s) in {time.time() - t0:.0f} s")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
