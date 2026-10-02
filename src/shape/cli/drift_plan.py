"""``shape generate-drift``: a generation schema plus a drift plan, one folder of tables per day.

The plan is a JSON file (``{"start": ..., "days": ..., "events": [...]}``, see
``docs/DRIFT.md``). Beside the days it writes each day's schema under ``_specs/`` and the answer key
``ground_truth.json``. Nothing heavy loads at import time (T-18).
"""

from __future__ import annotations

import argparse
import json
from typing import Any

_FORMATS = ("csv", "parquet", "jsonl")
_TARGET_HELP = (
    "an installed domain (see `shape list`) or a generation schema file "
    "(`shape from-ddl` writes one)"
)


def add_arguments(sub: Any) -> None:
    """Register ``generate-drift`` on the subparsers."""
    gd = sub.add_parser(
        "generate-drift",
        help="generate daily tables with planted drift and an answer key",
        description="Apply the events of PLAN.json (null-rate ramps, new categories, price "
        "steps, added columns, type changes, each a step, ramp or window) to the generation "
        "schema one day at a time, and write OUTPUT/<date>/<table>.<format> for every day, "
        "OUTPUT/_specs/<date>.json (the day's schema) and OUTPUT/ground_truth.json (every planted "
        "event). The same schema, plan and seed give the same files.",
    )
    gd.add_argument("target", metavar="DOMAIN|SCHEMA.json", help=_TARGET_HELP)
    gd.add_argument("plan", metavar="PLAN.json", help="the drift plan")
    gd.add_argument("--mode", choices=("3nf", "star"), help="the schema mode of a domain")
    gd.add_argument("-o", "--output", required=True, metavar="DIR", help="where the days go")
    gd.add_argument("--format", "-f", choices=_FORMATS, default="parquet", help="default: parquet")
    gd.add_argument("--days", type=int, help="the number of days (default: the plan's)")
    gd.add_argument("--start", metavar="YYYY-MM-DD", help="the first day (default: the plan's)")
    gd.add_argument("--scale", "-s", metavar="PRESET", help="the scale preset of each day")
    gd.add_argument(
        "--rows",
        action="append",
        default=[],
        metavar="TABLE=N",
        help="rows per day for one table (repeatable)",
    )
    gd.add_argument("--seed", type=int, help="the seed (default: the schema's)")
    gd.add_argument("--json", action="store_true", help="print the result as JSON")


def run(a: argparse.Namespace) -> int:
    from shape.cli.generation import _check_scale, load_target
    from shape.generation.drift_plan import DriftPlan, DriftPlanError

    schema = load_target(a.target, a.mode)
    _check_scale(schema, a.scale)
    try:
        with open(a.plan, encoding="utf-8") as fh:
            doc = json.load(fh)
    except json.JSONDecodeError as exc:
        raise DriftPlanError(f"drift plan {a.plan} is not valid JSON: {exc}") from exc
    if not isinstance(doc, dict):
        raise DriftPlanError("a drift plan must be a JSON object")
    if a.days is not None:
        doc["days"] = a.days
    if a.start is not None:
        doc["start"] = a.start
    plan = DriftPlan.from_dict(doc)
    rows: dict[str, int] = {}
    for item in a.rows:
        table, sep, n = item.partition("=")
        if not sep or not n.isdigit():
            raise ValueError(f"--rows wants TABLE=N, got {item!r}")
        if table not in schema.tables:
            raise ValueError(f"--rows: the schema has no table {table!r}")
        rows[table] = int(n)
    files = plan.write(
        schema, a.output, row_counts=rows or None, scale=a.scale, seed=a.seed, fmt=a.format
    )
    truth = plan.ground_truth()
    if a.json:
        print(
            json.dumps(
                {"output": a.output, "format": a.format, "days": plan.days, "files": len(files)}
                | {"events": [e["id"] for e in truth["events"]]},
                indent=2,
            )
        )
        return 0
    print(f"Planted {len(truth['events'])} events over {plan.days} days from {plan.start}")
    for e in truth["events"]:
        end = f" to {e['end']}" if e["end"] else ""
        print(f"  {e['id']}: {e['kind']} on {e['table']}.{e['column']} from {e['start']}{end}")
    print(f"\nWritten {len(files)} files to {a.output}/ (answer key: ground_truth.json)")
    return 0
