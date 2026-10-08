"""``shape pin``: pin the generator versions a generation spec uses, or check that it does.
See ``docs/GENERATION_STABILITY.md``."""

from __future__ import annotations

import json
import sys
from typing import Any


def add_parsers(sub: Any) -> None:
    p = sub.add_parser(
        "pin",
        help="pin the generator versions a generation spec uses (or check that it does)",
        description="Writes the current generator version of every strategy and distribution "
        "the spec uses into its `generators` map, so the same spec and seed give the same "
        "dataset id in every 1.x release. A pin that is already there is kept. Unknown fields "
        "of the spec are kept. `--check` writes nothing: it exits 1 and lists the names the "
        "spec uses but does not pin, 0 when all are pinned, 2 for a spec that is not valid.",
    )
    p.add_argument("spec", metavar="SPEC", help="a generation spec (JSON)")
    p.add_argument("-o", "--output", metavar="OUT", help="write here instead of over SPEC")
    p.add_argument("--check", action="store_true", help="list unpinned names, write nothing")
    p.add_argument("--json", action="store_true", help="print the result as JSON")


def run(a: Any) -> int:
    from shape.generation import pinning
    from shape.generation.spec_edit import SpecDocument

    if a.check and a.output:
        raise ValueError("--check writes nothing: it does not combine with -o")
    doc = SpecDocument.load(a.spec)
    report = pinning.inspect(doc, a.spec) if a.check else pinning.pin(doc, a.spec)
    for name in report.unused:
        print(f"shape: warning: {a.spec} pins {name}, which it does not use", file=sys.stderr)
    if a.check:
        if a.json:
            print(
                json.dumps(
                    {
                        "spec": a.spec,
                        "pinned": not report.unpinned,
                        "generators": report.pinned,
                        "unpinned": report.unpinned,
                        "unused": report.unused,
                    },
                    sort_keys=True,
                )
            )
        elif report.unpinned:
            print(f"{a.spec} does not pin: {', '.join(report.unpinned)}")
        else:
            print(f"{a.spec} pins all {len(report.pinned)} generators it uses")
        return 1 if report.unpinned else 0
    target = a.output or a.spec
    if report.added or a.output:
        doc.save(target)
    if a.json:
        print(
            json.dumps(
                {
                    "spec": a.spec,
                    "written": target if (report.added or a.output) else None,
                    "generators": report.pinned,
                    "added": report.added,
                    "unused": report.unused,
                },
                sort_keys=True,
            )
        )
    elif report.added:
        names = ", ".join(f"{n}={v}" for n, v in report.added.items())
        print(f"pinned {len(report.added)} generators in {target}: {names}")
    else:
        print(f"{a.spec} already pins all {len(report.pinned)} generators it uses")
    return 0
