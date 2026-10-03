"""``shape types PROFILE.shape``: where declared, inferred and contract types disagree (W2-07).

Nothing heavy loads at import time (T-18); the command imports what it needs when it runs.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any


def add_parser(sub: Any) -> None:
    p = sub.add_parser(
        "types",
        help="where a profile's declared, inferred and contract types disagree",
        description="List the columns whose types need a second look: a declared type that "
        "differs from what the values hold (a string of integers, a float of whole numbers, "
        "a string of ISO dates), an integer column the identifier rule calls a suspect (a ZIP "
        "code, an NPI), an inferred type that fewer than --min-confidence of the values fit, and "
        "(with --contract) a contract type that differs from the profile's. Each line names the "
        "table, the column, both types, the confidence and the option that would change it. "
        "Exit 0 with nothing to report, 1 with findings, 2 for bad input.",
    )
    p.add_argument("profile", metavar="PROFILE.shape")
    p.add_argument(
        "--contract", metavar="CONTRACT.json", help="also compare the contract's column types"
    )
    p.add_argument(
        "--min-confidence",
        type=float,
        default=None,
        metavar="C",
        help="report an inferred type that fewer than this share of the values fit "
        "(0 to 1, default 0.99)",
    )
    p.add_argument("--json", action="store_true", help="print the findings as a JSON list")


def _line(f: dict[str, Any]) -> str:
    confidence = "n/a" if f["confidence"] is None else f"{f['confidence']:.2f}"
    return (
        f"{f['table']}.{f['column']}  {f['kind']}: declared {f['declared'] or '-'}, "
        f"inferred {f['inferred'] or '-'}, confidence {confidence}; option {f['option']}"
    )


def run(a: argparse.Namespace) -> int:
    import shape
    from shape.profile.types_report import MIN_CONFIDENCE, has_records, types_report

    prof = shape.load(a.profile)
    threshold = MIN_CONFIDENCE if a.min_confidence is None else a.min_confidence
    findings = types_report(prof, contract=a.contract, min_confidence=threshold)
    if not has_records(prof):
        print(
            "shape: note: type inference is not recorded in this profile (it was written by an "
            "older Shape); re-profile the data to see declared and inferred types",
            file=sys.stderr,
        )
    if a.json:
        json.dump(findings, sys.stdout, indent=2, allow_nan=False)
        print()
    elif not findings:
        print("No type findings.")
    else:
        for f in findings:
            print(_line(f))
            print(f"    {f['message'].split(': ', 1)[1]}")
        print(f"{len(findings)} finding(s).")
    return 1 if findings else 0
