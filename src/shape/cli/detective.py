"""``shape detective list|start|hint|check``: data detective packs.

``start NAME -o DIR`` writes a case (the data, the brief and the profile of the clean batch, never
the findings); ``hint NAME N`` prints a hint; ``check NAME --answer ANSWER.json`` compares an
answer with the planted findings. ``check`` exits 0 when every planted finding is named and none
that is not planted, 1 otherwise, 2 for a malformed answer or an unknown pack. See
``docs/DETECTIVE.md``. Nothing heavy loads at import time (T-18).
"""

from __future__ import annotations

import argparse
import json
from typing import Any


def add_arguments(sub: Any) -> None:
    """Register ``detective`` on the subparsers."""
    dt = sub.add_parser(
        "detective",
        help="play data detective: find the problems planted in a generated batch",
        description="Data detective packs train you to find planted problems with Shape's own "
        "commands. `start` generates a case; look at it with `shape profile`, `shape diff` and "
        "`shape check`; name what you found in an answer file and `check` it. The data is "
        "generated from a library scenario, never shipped. See docs/DETECTIVE.md.",
    )
    actions = dt.add_subparsers(
        dest="detective_cmd", required=True, metavar="{list,start,hint,check}"
    )
    actions.add_parser("list", help="list the packs", description=dt.description)
    st = actions.add_parser("start", help="write a case to a folder", description=dt.description)
    st.add_argument("name", metavar="NAME", help="a pack name (see `shape detective list`)")
    st.add_argument("-o", "--output", required=True, metavar="DIR", help="a new or empty folder")
    hi = actions.add_parser("hint", help="print one hint of a pack", description=dt.description)
    hi.add_argument("name", metavar="NAME", help="a pack name")
    hi.add_argument("n", metavar="N", type=int, help="the hint number, counting from 1")
    ck = actions.add_parser(
        "check", help="check an answer against the planted findings", description=dt.description
    )
    ck.add_argument("name", metavar="NAME", help="a pack name")
    ck.add_argument("--answer", required=True, metavar="ANSWER.json", help="the answer file")


def run(a: argparse.Namespace) -> int:
    from shape.scenario import detective

    if a.detective_cmd == "list":
        packs = detective.list_packs()
        if a.json:
            rows = [
                {
                    "name": p["name"],
                    "level": p["level"],
                    "brief": p["brief"],
                    "hints": len(p["hints"]),
                    "problems": len(p["findings"]),
                }
                for p in packs
            ]
            print(json.dumps({"packs": rows}, indent=2))
            return 0
        for p in packs:
            n = len(p["findings"])
            print(f"{p['name']:<18} {p['level']:<13} {n} problem{'s' if n != 1 else ''}")
            print(f"    {p['brief'].split('. ')[0].rstrip('.')}.")
        return 0
    if a.detective_cmd == "start":
        case = detective.start(a.name, a.output)
        if a.json:
            print(json.dumps(case.to_dict(), indent=2))
            return 0
        print(f"case {case.pack}: {len(case.data)} tables written to {case.directory}/data")
        print(f"read {case.brief}, then look at the data with shape profile, diff and check")
        return 0
    if a.detective_cmd == "hint":
        text = detective.hint(a.name, a.n)
        print(json.dumps({"pack": a.name, "hint": a.n, "text": text}) if a.json else text)
        return 0
    pack = detective.load_pack(a.name)
    verdict = detective.check_answer(pack, detective.load_answer(a.answer))
    if a.json:
        print(json.dumps(verdict.to_dict(), indent=2))
        return 0 if verdict.solved else 1
    for label, rows in (
        ("found", verdict.found),
        ("missed", verdict.missed),
        ("wrong", verdict.wrong),
    ):
        for f in rows:
            where = f["table"] + (f".{f['column']}" if f["column"] else "")
            print(f"{label:<7} {where}: {f['mode']}")
    print("solved" if verdict.solved else "not solved")
    return 0 if verdict.solved else 1
