"""``shape suite run NAME|FILE``: run a named suite of library scenarios and compare each outcome
with its answer key.

Exit 0 when every scenario met its answer key, 1 when one did not (the scenario, the expectation
and what was observed are printed), 2 for a malformed suite or an unknown scenario. Nothing heavy
loads at import time (T-18).
"""

from __future__ import annotations

import argparse
import json
from typing import Any


def add_arguments(sub: Any) -> None:
    """Register ``suite`` on the subparsers."""
    su = sub.add_parser(
        "suite",
        help="run a named suite of starter scenarios and check each against its answer key",
        description="Run every scenario of a suite (the built-in suites are smoke and "
        "schema-evolution, or give the path of a shape-suite file), compare each outcome with the "
        "scenario's answer key and print the result. Exit 0 when every scenario met its "
        "expectation, 1 when one did not, 2 for a malformed suite or an unknown scenario. See "
        "docs/SCENARIO_LIBRARY.md.",
    )
    actions = su.add_subparsers(dest="suite_cmd", required=True, metavar="{run}")
    ru = actions.add_parser("run", help="run a suite", description=su.description)
    ru.add_argument("suite", metavar="NAME|FILE", help="a built-in suite name or a suite file")
    ru.add_argument(
        "--scale",
        metavar="PRESET",
        default="small",
        help="a scale preset of the scenarios' domain, or tiny (default: small)",
    )
    ru.add_argument("--seed", type=int, help="seed (default: each scenario's own)")
    ru.add_argument(
        "-o",
        "--output",
        metavar="DIR",
        help="write each scenario's tables under DIR/<scenario>/ (default: write nothing)",
    )
    ru.add_argument("--json", action="store_true", help="print the result as JSON")


def run(a: argparse.Namespace) -> int:
    from shape.scenario.library import run_suite

    result = run_suite(a.suite, scale=a.scale, seed=a.seed, output=a.output)
    if a.json:
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True, default=str))
        return 0 if result.met else 1
    print(f"suite {result.name} (scale {result.scale})")
    for r in result.results:
        o = r.outcome
        print(f"  {'ok  ' if r.met else 'FAIL'} {o.scenario} ({o.elapsed_seconds:.1f}s)")
        for m in r.mismatches:
            print(f"       scenario {o.scenario}: expected {m.expected}; observed {m.observed}")
    met = sum(r.met for r in result.results)
    print(f"{met} of {len(result.results)} scenarios met their answer key")
    return 0 if result.met else 1
