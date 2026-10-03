"""``shape failure-modes list|show``: the failure mode catalog.

``list`` prints every entry (``--json``: the whole catalog), ``show ID`` prints one with its
symptoms, causes, the checks that catch it and the scenario that reproduces it. Exit 0, or 2 for an
unknown id. See ``docs/FAILURE_MODES.md``. Nothing heavy loads at import time (T-18).
"""

from __future__ import annotations

import argparse
import json
from typing import Any


def add_arguments(sub: Any) -> None:
    """Register ``failure-modes`` on the subparsers."""
    fm = sub.add_parser(
        "failure-modes",
        help="the catalog of ways data goes wrong, and which check catches each",
        description="The failure mode catalog: late-arriving records, schema changes, null floods "
        "and the rest, each with its symptoms, common causes, the Shape checks that detect it and "
        "a library scenario that reproduces it (`shape pack run library:NAME`). The suite "
        "`shape suite run failure-modes` checks every entry against what Shape reports. See "
        "docs/FAILURE_MODES.md.",
    )
    actions = fm.add_subparsers(dest="failure_modes_cmd", required=True, metavar="{list,show}")
    actions.add_parser("list", help="list the failure modes", description=fm.description)
    show = actions.add_parser("show", help="show one failure mode", description=fm.description)
    show.add_argument("id", metavar="ID", help="a failure mode id, such as null-flood")


def _text(mode: dict[str, Any]) -> str:
    lines = [f"{mode['id']}: {mode['title']} (severity {mode['severity']})", "", "Symptoms:"]
    lines += [f"  - {s}" for s in mode["symptoms"]]
    lines += ["", "Common causes:"] + [f"  - {c}" for c in mode["common_causes"]]
    detected = mode["detected_by"]
    lines += ["", "Detected by: " + (", ".join(detected) if detected else "no Shape check")]
    if mode.get("gap"):
        lines.append(f"  {mode['gap']}")
    lines += ["", f"Reproduce: shape pack run {mode['reproduce']}"]
    return "\n".join(lines)


def run(a: argparse.Namespace) -> int:
    from shape.scenario.library import catalog

    if a.failure_modes_cmd == "show":
        mode = catalog.get_mode(a.id)
        print(json.dumps(mode, indent=2) if a.json else _text(mode))
        return 0
    modes = catalog.load_catalog()
    if a.json:
        print(json.dumps({"modes": modes}, indent=2))
        return 0
    width = max(len(m["id"]) for m in modes)
    for m in modes:
        checks = ", ".join(m["detected_by"]) or "(no Shape check)"
        print(f"{m['id']:<{width}}  {m['severity']:<8}  {m['title']}  [{checks}]")
    print(f"{len(modes)} failure modes")
    return 0
