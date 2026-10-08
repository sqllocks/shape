"""``shape parity A B``: do two environments have the same shape? See ``docs/PARITY.md``."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any


def add_arguments(sub: Any) -> None:
    from shape.cli.project import add_project_flags

    p = sub.add_parser(
        "parity",
        help="check that environment B has the same shape as environment A",
        description="Compare two environments by shape: the same tables and columns, types, "
        "keys and relationships, similar null rates and distributions, and table sizes in the "
        "same proportions. A and B are data (a file or a folder), a .shape profile, a "
        "`shape profile export` file or a `shape profile safe` file. A check the inputs cannot "
        "support is reported as not measured, never as a pass. Exit 0 parity, 1 a check "
        "failed, 2 unusable input.",
    )
    p.add_argument("a", metavar="A", help="the reference side (for example production)")
    p.add_argument("b", metavar="B", help="the side under test (for example development)")
    p.add_argument(
        "--dataset",
        action="store_true",
        help="a folder is several tables, one per file (as for `shape profile`)",
    )
    p.add_argument(
        "--scaled",
        action="store_true",
        help="compare each table's share of the total row count, so a smaller environment "
        "with the same proportions passes",
    )
    p.add_argument(
        "--row-tolerance",
        type=float,
        default=None,
        metavar="F",
        help="how far row counts (or, with --scaled, shares) may differ, default 0.1",
    )
    p.add_argument("--tables", metavar="T,...", help="compare only these tables")
    p.add_argument("-o", "--output", metavar="REPORT.json", help="write the report as JSON")
    p.add_argument("--json", action="store_true", help="print the report as JSON, not text")
    add_project_flags(p)


def _tables(raw: str | None) -> list[str] | None:
    if raw is None:
        return None
    names = [n.strip() for n in raw.split(",") if n.strip()]
    if not names:
        raise ValueError("--tables needs at least one table name")
    return names


def run(a: argparse.Namespace) -> int:
    from shape.cli import project as project_cli
    from shape.drift.engine import resolve_policy
    from shape.parity import DEFAULT_ROW_TOLERANCE, build_report, compare, load_side, render_text

    tolerance = DEFAULT_ROW_TOLERANCE if a.row_tolerance is None else a.row_tolerance
    if tolerance < 0 or tolerance != tolerance:
        raise ValueError("--row-tolerance must be a number of 0 or more")
    tables = _tables(a.tables)
    ctx = project_cli.context(a)
    source = ctx.source if ctx else None
    if getattr(a, "source", None) and source is None:
        raise ValueError(f"--source {a.source!r} needs a shape.yml that declares it")
    policy = resolve_policy(policy=source.drift_policy() or None if source else None)
    side_a = load_side(a.a, dataset=a.dataset)
    side_b = load_side(a.b, dataset=a.dataset)
    owner = source.owner_of if source else (lambda table, column: None)
    checks = compare(
        side_a,
        side_b,
        policy=policy,
        row_tolerance=tolerance,
        scaled=a.scaled,
        tables=tables,
        owner=lambda table, column: owner(column, table) if column else None,
    )
    options: dict[str, Any] = {
        "scaled": bool(a.scaled),
        "row_tolerance": tolerance,
        "tables": tables,
        "source": source.name if source else None,
        "project": str(ctx.project.path) if ctx else None,
    }
    report = build_report(side_a, side_b, checks, options)
    if a.output:
        from pathlib import Path

        from shape.registry.profiles import atomic_write_text

        atomic_write_text(
            Path(a.output), json.dumps(report, indent=2, allow_nan=False) + "\n", newline="\n"
        )
    if a.json:
        print(json.dumps(report, sort_keys=True, allow_nan=False))
    else:
        sys.stdout.write(render_text(report))
    return 0 if report["parity"] else 1
