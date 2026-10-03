"""``shape gameday run PLAN.json -o DIR``: rehearse failures against local copies of your data.

For each round of the plan the runner copies the named tables into ``DIR/round-N/``, plants a
failure there, runs the round's checks (only ``profile``, ``diff``, ``check``, ``verify`` and
``fidelity``) and records whether each expected detection happened. It writes
``DIR/gameday_report.json`` and ``DIR/gameday_report.md`` and exits 0 when every expectation was
detected, 1 when one was missed, 2 for a malformed plan. The source folder is never modified. See
``docs/GAMEDAY.md``. Nothing heavy loads at import time (T-18).
"""

from __future__ import annotations

import argparse
import json
from typing import Any


def add_arguments(sub: Any) -> None:
    """Register ``gameday`` on the subparsers."""
    gd = sub.add_parser(
        "gameday",
        help="rehearse failures against local copies of your data and see which checks catch them",
        description="A game day plants failures of the catalog in copies of your own local data "
        "and runs the checks you list, so you learn what your checks would and would not "
        "notice before a real failure teaches you. Only local files and folders are used; the "
        "source data is never modified. See docs/GAMEDAY.md.",
    )
    actions = gd.add_subparsers(dest="gameday_cmd", required=True, metavar="{run}")
    ru = actions.add_parser("run", help="run a plan", description=gd.description)
    ru.add_argument("plan", metavar="PLAN.json", help="a shape-gameday plan")
    ru.add_argument("-o", "--output", required=True, metavar="DIR", help="a new or empty folder")
    ru.add_argument("--seed", type=int, default=42, help="seed of the injections (default: 42)")
    ru.add_argument(
        "--dry-run",
        action="store_true",
        help="check the plan, read the tables and print what each round would plant; write "
        "and run nothing",
    )


def run(a: argparse.Namespace) -> int:
    from shape.scenario import gameday

    report = gameday.run(a.plan, a.output, seed=a.seed, dry_run=a.dry_run)
    if a.json:
        print(json.dumps(report.to_dict(), indent=2))
        return 0 if (a.dry_run or report.detected) else 1
    for r in report.rounds:
        status = "would plant" if a.dry_run else ("detected" if r.detected else "MISSED")
        print(f"round {r.name}: {status} {r.inject} in {', '.join(r.tables)}")
        for i in r.injected:
            where = f"{i.table}.{i.column}" if i.column else i.table
            print(f"    {i.kind} on {where}")
        for e in r.expectations:
            print(f"    {'detected' if e['detected'] else 'MISSED  '} {e['expect']}")
    if a.dry_run:
        print("dry run: nothing was written or run")
        return 0
    print(f"report: {a.output}/gameday_report.md")
    return 0 if report.detected else 1
