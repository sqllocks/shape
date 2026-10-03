"""``shape bridge``: serve the JSON request/response protocol on standard input and output, and
print or check its published schemas (P6-11).

``shape bridge`` reads one request per line and answers one line per request until end of input.
``shape bridge --once`` reads all of standard input as one request. ``shape bridge schema --out
DIR`` writes the JSON Schemas; ``--check DIR`` exits 1 when the files in DIR differ from them.
The protocol is documented in ``docs/BRIDGE.md``.

Nothing heavy loads at import time (T-18).
"""

from __future__ import annotations

import argparse
import sys
from typing import Any


def add_arguments(sub: Any) -> None:
    br = sub.add_parser(
        "bridge",
        help="serve the JSON request/response protocol on standard input and output",
        description="Serve Shape's commands as a versioned JSON protocol on standard input and "
        "output, for editors, notebooks, wrappers and agents. One request per line, one response "
        "per line. See docs/BRIDGE.md.",
    )
    br.add_argument(
        "--jobs-dir",
        metavar="DIR",
        help="where job state is kept, so jobs outlive this process "
        "(default: $SHAPE_JOBS_DIR, else ~/.shape/jobs)",
    )
    br.add_argument(
        "--once",
        action="store_true",
        help="read all of standard input as one request and answer once (exit 1 on an error)",
    )
    actions = br.add_subparsers(dest="bridge_cmd")
    sc = actions.add_parser("schema", help="write or check the published JSON Schemas")
    group = sc.add_mutually_exclusive_group(required=True)
    group.add_argument("--out", metavar="DIR", help="write the schemas into DIR")
    group.add_argument("--check", metavar="DIR", help="exit 1 if DIR differs from the schemas")


def run(a: argparse.Namespace) -> int:
    if a.bridge_cmd == "schema":
        from shape.bridge.schemas import check_schemas, write_schemas

        if a.out:
            written = write_schemas(a.out)
            print(f"wrote {len(written)} schema files to {a.out}")
            return 0
        problems = check_schemas(a.check)
        for problem in problems:
            print(f"shape: {problem}", file=sys.stderr)
        if problems:
            print(
                f"shape: {len(problems)} difference(s): regenerate with "
                f"`shape bridge schema --out {a.check}`",
                file=sys.stderr,
            )
        return 1 if problems else 0
    from shape.bridge.core import Bridge
    from shape.bridge.server import serve

    return serve(Bridge(a.jobs_dir), once=a.once)
