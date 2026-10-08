"""``shape skew-rehearsal PROFILE.shape --schema SCHEMA --scale S -o DIR`` (W5-10, docs/SCALE.md).

Exit 0 within tolerance, 1 outside it, 2 when the profile has no frequency data for a requested
column (or other bad input). Nothing heavy loads at import time (T-18).
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any


def add_arguments(sub: Any) -> None:
    p = sub.add_parser(
        "skew-rehearsal",
        help="generate a schema at scale with the profile's key skew and check it",
        description="Generate SCHEMA at scale S with the foreign-key columns drawn with the "
        "concentration the profile measured (the share of rows held by the most frequent keys), "
        "write the tables and DIR/skew_report.json, and compare the profile's top share with the "
        "generated one per column. See docs/SCALE.md.",
    )
    p.add_argument("profile", metavar="PROFILE.shape")
    p.add_argument(
        "--schema", required=True, metavar="DOMAIN|SCHEMA.json", help="the generation schema"
    )
    p.add_argument("--scale", required=True, metavar="S", help="the scale preset (shape presets)")
    p.add_argument("--seed", type=int, metavar="N", help="the seed (default: the schema's)")
    p.add_argument("-o", "--output", required=True, metavar="DIR")
    p.add_argument(
        "--columns",
        metavar="T.C,...",
        help="the columns to skew (default: every foreign key the profile has frequency data for)",
    )
    p.add_argument(
        "--tolerance",
        type=float,
        default=0.02,
        metavar="T",
        help="largest allowed absolute difference of the top shares (default 0.02)",
    )


def run(a: argparse.Namespace) -> int:
    import shape
    from shape.cli.generation import _check_scale, load_target
    from shape.skew import rehearse

    columns = [c.strip() for c in a.columns.split(",") if c.strip()] if a.columns else None
    if a.columns is not None and not columns:
        raise ValueError("--columns names no column")
    profile = shape.load(a.profile)
    schema = load_target(a.schema)
    _check_scale(schema, a.scale)
    result = rehearse(
        profile,
        schema,
        a.scale,
        seed=a.seed,
        columns=columns,
        tolerance=a.tolerance,
        out_dir=a.output,
    )
    for c in result.columns:
        print(
            f"{c.column}: profile top {c.profile_top_share:.4f}, generated "
            f"{c.generated_top_share:.4f} (top {c.top_fraction:.4f} of {c.parents:,} parents) "
            f"{'ok' if c.within else 'OUTSIDE tolerance'}"
        )
    for s in result.skipped:
        print(f"skipped {s['column']}: {s['reason']}", file=sys.stderr)
    print(json.dumps({"report": f"{a.output}/skew_report.json", "within_tolerance": result.within}))
    return 0 if result.within else 1
