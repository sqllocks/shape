"""``shape seed SPEC|DOMAIN --target URI``: write generated tables into a test database (or a
folder of INSERT scripts) in foreign-key order. See ``docs/TESTING_WITH_SHAPE.md``.

Exit 0 when seeded (or planned, with ``--dry-run``), 1 when ``--mode create`` found an existing
table (nothing written), 2 for bad input or a failed write. Nothing heavy loads at import time
(T-18).
"""

from __future__ import annotations

import argparse
import json
from typing import Any


def add_arguments(sub: Any) -> None:
    """Register ``seed`` on the subparsers."""
    sd = sub.add_parser(
        "seed",
        help="write generated tables into a test database, in foreign-key order",
        description="Generate the tables of a domain or generation schema and write each one "
        "through the database sink of the target's scheme (mssql or sqlserver, postgres or "
        "postgresql, mysql), parents before the tables that point at them. A sql://DIR target "
        "writes one ordered INSERT script per table instead. A table is one transaction (the sinks "
        "commit per table). --mode create (the default) refuses an existing table and writes "
        "nothing; truncate empties the tables first, so the same seed gives the same contents; "
        "append adds rows. --dry-run prints the plan and connects to nothing. Passwords are never "
        "part of the URI: use the sink's password environment variable.",
    )
    sd.add_argument("spec", metavar="SPEC|DOMAIN", help="a generation schema file or a domain")
    sd.add_argument("--target", required=True, metavar="URI", help="the database URI or sql://DIR")
    sd.add_argument("--scale", "-s", metavar="PRESET", help="a scale preset, or tiny")
    sd.add_argument("--seed", type=int, help="the seed (default: the schema's)")
    sd.add_argument(
        "--mode",
        choices=("create", "truncate", "append"),
        default="create",
        help="create refuses existing tables (default), truncate empties them, append adds rows",
    )
    sd.add_argument("--dry-run", action="store_true", help="print the plan; connect to nothing")
    sd.add_argument("--json", action="store_true", help="print the result as JSON")


def run(a: argparse.Namespace) -> int:
    from shape.testdata.seed import SeedRefused, seed_target

    try:
        result = seed_target(
            a.spec, a.target, scale=a.scale, seed=a.seed, mode=a.mode, dry_run=a.dry_run
        )
    except SeedRefused as exc:
        if a.json:
            print(json.dumps({"refused": str(exc)}, indent=2))
        else:
            print(f"shape: seed refused: {exc}")
        return 1
    if a.json:
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True, default=str))
        return 0
    if result.dry_run:
        print("\n".join(result.plan.lines()))
        print("dry run: nothing was connected to or written")
        return 0
    for name, rows in result.written.items():
        print(f"  {name}: {rows:,} rows")
    print(f"seeded {sum(result.written.values()):,} rows into {len(result.written)} tables")
    return 0
