"""``shape canary make|check``: canaries that prove a pipeline's checks still fire.

``make (library:NAME | ID) -o DIR`` writes a small marked batch with planted failures and
``DIR/canary.json``; you send the batch through a real pipeline input. ``check canary.json
--result RESULT.json...`` reads the ``--json`` results of your own checks and exits 0 when every
expected detection is present, 1 when one is missing (named as a blind spot), 2 for malformed
input. See ``docs/CANARIES.md``. Nothing heavy loads at import time (T-18).
"""

from __future__ import annotations

import argparse
import json
from typing import Any


def add_arguments(sub: Any) -> None:
    """Register ``canary`` on the subparsers."""
    ca = sub.add_parser(
        "canary",
        help="prove your pipeline's checks still fire, with a marked batch of planted failures",
        description="A canary is a small synthetic batch with known planted failures, every row "
        "marked so that downstream jobs can filter it out. Send it through a real pipeline input "
        "on a schedule: if your checks do not flag it, your monitoring is blind. `make` writes "
        "the batch to a local folder; `check` compares the results of your own `shape diff`, "
        "`shape check` and `shape verify` (run with --json) with what the canary expects. See "
        "docs/CANARIES.md.",
    )
    actions = ca.add_subparsers(dest="canary_cmd", required=True, metavar="{make,check}")
    mk = actions.add_parser("make", help="write a canary batch", description=ca.description)
    mk.add_argument(
        "target", metavar="library:NAME|ID", help="a scenario of the library or a failure mode id"
    )
    mk.add_argument("-o", "--output", required=True, metavar="DIR", help="a new or empty folder")
    mk.add_argument("--rows", type=int, default=1000, metavar="N", help="rows per table (1000)")
    mk.add_argument("--seed", type=int, help="seed (default: the scenario's)")
    mk.add_argument(
        "--marker",
        default="shape_canary=1",
        metavar="COLUMN=VALUE",
        help="the column every row carries, and its value (default: shape_canary=1)",
    )
    mk.add_argument(
        "--format", choices=("csv", "parquet", "jsonl"), default="csv", help="file format (csv)"
    )
    mk.add_argument(
        "--dry-run", action="store_true", help="print what would be written; write nothing"
    )
    ck = actions.add_parser(
        "check",
        help="check results against a canary's expected detections",
        description=ca.description,
    )
    ck.add_argument("canary", metavar="canary.json", help="the canary document")
    ck.add_argument(
        "--result",
        action="append",
        required=True,
        metavar="RESULT.json",
        help="a shape-result document of your own diff, check or verify (repeatable)",
    )


def run(a: argparse.Namespace) -> int:
    from shape.scenario import canary

    if a.canary_cmd == "make":
        marker = canary.parse_marker(a.marker)
        plan = canary.make(
            a.target,
            a.output,
            rows=a.rows,
            seed=a.seed,
            marker=marker,
            fmt=a.format,
            dry_run=a.dry_run,
        )
        if a.json:
            print(json.dumps(plan.to_dict(), indent=2))
            return 0
        verb = "would write" if plan.dry_run else "wrote"
        for path in plan.files:
            print(f"{verb} {path}")
        print(f"expects: {', '.join(plan.expected)}")
        return 0
    doc = canary.load_canary(a.canary)
    docs, names = canary.load_results(a.result)
    report = canary.check(doc, docs, names)
    if a.json:
        print(json.dumps(report.to_dict(), indent=2))
        return 0 if report.ok else 1
    for item in report.present:
        print(f"detected  {item}")
    for item in report.blind_spots:
        print(f"BLIND SPOT  {item}: your checks did not flag it")
    print("every expected detection is present" if report.ok else "your monitoring is blind")
    return 0 if report.ok else 1
