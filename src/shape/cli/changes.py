"""``shape changes validate|list|add|ack``: the planned-change file (docs/PLANNED_CHANGES.md).

Exit codes: 0 done, 1 ``validate`` found problems, 2 bad input (a missing or unreadable file, a
duplicate id, an invalid entry, a bad selection).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from shape.project.changes import (
    DEFAULT_NAME,
    ChangesError,
    PlannedChanges,
    add_entries,
    load_changes,
    parse_changes,
    parse_day,
    today,
)


def add_arguments(sub: Any) -> None:
    c = sub.add_parser(
        "changes",
        help="the planned-change file: shape changes validate|list|add|ack",
        description="Planned changes are reviewed, dated exceptions that `shape diff`, "
        "`shape check` and `shape verify` read (docs/PLANNED_CHANGES.md).",
    )
    acts = c.add_subparsers(dest="changes_cmd", metavar="ACTION", required=True)
    v = acts.add_parser(
        "validate",
        help="check the file and report every problem with the entry id and key",
        description="Exit 0 when valid, 1 with each problem on its own line, 2 when the file "
        "is missing or unreadable.",
    )
    v.add_argument("file", metavar="FILE", nargs="?", help="default: the project's changes file")
    v.add_argument("--json", action="store_true", help="print the result as JSON")
    ls = acts.add_parser("list", help="list the entries")
    ls.add_argument("file", metavar="FILE", nargs="?", help="default: the project's changes file")
    ls.add_argument("--active-on", metavar="YYYY-MM-DD", help="only entries active on this day")
    ls.add_argument("--json", action="store_true", help="print the result as JSON")
    ad = acts.add_parser(
        "add",
        help="append a planned change (comments and existing entries stay as they are)",
        description="Append one entry. A duplicate id, an unknown kind or any invalid entry "
        "exits 2 and writes nothing. The file is created when it does not exist.",
    )
    _file_flag(ad)
    ad.add_argument("--id", required=True, metavar="ID")
    ad.add_argument("--column", required=True, metavar="COL", help="name, table.column, glob or *")
    ad.add_argument(
        "--kind",
        action="append",
        default=[],
        metavar="KIND",
        help="a drift kind or contract rule (repeatable)",
    )
    ad.add_argument("--until", required=True, metavar="YYYY-MM-DD")
    ad.add_argument("--reason", required=True, metavar="TEXT")
    ad.add_argument("--from", dest="from_", metavar="YYYY-MM-DD", help="default: today (UTC)")
    ad.add_argument("--action", choices=("expect", "suppress", "severity"), default="expect")
    ad.add_argument("--severity", choices=("low", "medium", "high"))
    ad.add_argument("--source", metavar="NAME")
    ad.add_argument("--owner", metavar="NAME")
    ad.add_argument("--ticket", metavar="ID")
    ak = acts.add_parser(
        "ack",
        help="turn changes of a `shape diff --json` result into expect entries",
        description="Acknowledge reported changes: each selected change becomes an `expect` "
        "entry with acknowledged_by and acknowledged_at.",
    )
    ak.add_argument("result", metavar="RESULT.json", help="the output of `shape diff --json`")
    _file_flag(ak)
    ak.add_argument("--until", required=True, metavar="YYYY-MM-DD")
    ak.add_argument("--reason", required=True, metavar="TEXT")
    ak.add_argument("--by", required=True, metavar="NAME", help="who acknowledges")
    ak.add_argument("--change", type=int, action="append", default=[], metavar="N")
    ak.add_argument("--all", action="store_true", help="every change that is not already planned")


def _file_flag(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--file",
        metavar="FILE",
        help=f"the planned-change file to write (default: the project's, else {DEFAULT_NAME} "
        "next to shape.yml, else here)",
    )


def _dump(obj: Any) -> None:
    print(json.dumps(obj, sort_keys=True, default=str))


def _default_path(*, must_exist: bool) -> Path:
    from shape.project import find_project, load_project

    project_file = find_project()
    if project_file is not None:
        found = load_project(project_file).changes_file()
        if found is not None:
            return found
        if not must_exist:
            return project_file.parent / DEFAULT_NAME
    here = Path(DEFAULT_NAME)
    if must_exist and not here.is_file():
        raise FileNotFoundError(
            f"no planned-change file: pass FILE, set `changes` in shape.yml, or create "
            f"{DEFAULT_NAME}"
        )
    return here


def _validate(a: argparse.Namespace) -> int:
    path = Path(a.file) if a.file else _default_path(must_exist=True)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        print(f"shape: error: {path}: cannot read: {exc}", file=sys.stderr)
        return 2
    try:
        plan = parse_changes(text, path)
    except ChangesError as exc:
        if a.json:
            _dump({"valid": False, "file": str(path), "problems": list(exc.problems)})
        else:
            for problem in exc.problems:
                print(f"shape: error: {path}: {problem}", file=sys.stderr)
        return 1
    _dump(
        {
            "valid": True,
            "file": str(path),
            "format": "shape-planned-changes",
            "version": plan.version,
            "entries": len(plan.entries),
        }
    )
    return 0


def _list(a: argparse.Namespace) -> int:
    path = Path(a.file) if a.file else _default_path(must_exist=True)
    plan = load_changes(path)
    entries = plan.entries
    if a.active_on:
        day = parse_day(a.active_on, "--active-on")
        entries = tuple(e for e in entries if e.active_on(day))
    if a.json:
        _dump({"file": str(path), "changes": [e.to_dict() for e in entries]})
        return 0
    for e in entries:
        window = f"{e.from_ or '..'}..{e.until}"
        print(f"{e.id}  {e.action}  {e.column}  {','.join(e.kinds)}  {window}  {e.reason}")
    return 0


def _unique_id(base: str, taken: set[str]) -> str:
    n, ident = 1, base
    while ident in taken:
        n += 1
        ident = f"{base}-{n}"
    taken.add(ident)
    return ident


def _existing_ids(path: Path) -> set[str]:
    return {e.id for e in load_changes(path).entries} if path.exists() else set()


def _project_source_check(source: str | None) -> None:
    if source is None:
        return
    from shape.project import find_project, load_project

    found = find_project()
    if found is not None:
        load_project(found).source(source)  # unknown source: a ProjectError


def _add(a: argparse.Namespace) -> int:
    path = Path(a.file) if a.file else _default_path(must_exist=False)
    _project_source_check(a.source)
    raw: dict[str, Any] = {
        "id": a.id,
        "source": a.source,
        "column": a.column,
        "kinds": list(a.kind),
        "from": parse_day(a.from_, "--from").isoformat() if a.from_ else today().isoformat(),
        "until": parse_day(a.until, "--until").isoformat(),
        "action": a.action,
        "severity": a.severity,
        "reason": a.reason,
        "owner": a.owner,
        "ticket": a.ticket,
    }
    plan = add_entries(path, [raw])
    print(
        f"shape: added planned change {a.id} to {path} ({len(plan.entries)} entries)",
        file=sys.stderr,
    )
    return 0


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9._-]+", "-", text.lower()).strip("-.") or "x"


def _ack(a: argparse.Namespace) -> int:
    if a.all == bool(a.change):
        raise ValueError("ack needs exactly one of --all or --change N (repeatable)")
    try:
        with open(a.result, encoding="utf-8") as fh:
            result = json.load(fh)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"{a.result} is not a shape diff --json result: {exc}") from None
    changes = result.get("changes") if isinstance(result, dict) else None
    if not isinstance(changes, list):
        raise ValueError(f"{a.result} is not a shape diff --json result (no `changes` list)")
    if a.all:
        picked = [i for i, c in enumerate(changes, 1) if "planned" not in c]
        if not picked:
            raise ValueError("--all: there is no unplanned change in the result")
    else:
        picked = list(a.change)
        for n in picked:
            if not 1 <= n <= len(changes):
                raise ValueError(f"--change {n}: the result has {len(changes)} changes (1-based)")
    path = Path(a.file) if a.file else _default_path(must_exist=False)
    day = today().isoformat()
    source = (result.get("project") or {}).get("source")
    taken = _existing_ids(path)
    raws = []
    for n in picked:
        c = changes[n - 1]
        column = c.get("column") or "*"
        ident = _unique_id(_slug(f"ack-{day}-{column}-{c['kind']}"), taken)
        raws.append(
            {
                "id": ident,
                "source": source,
                "column": column,
                "kinds": [c["kind"]],
                "from": day,
                "until": parse_day(a.until, "--until").isoformat(),
                "action": "expect",
                "reason": a.reason,
                "acknowledged_by": a.by,
                "acknowledged_at": day,
            }
        )
    plan: PlannedChanges = add_entries(path, raws)
    print(
        f"shape: acknowledged {len(raws)} change(s) in {path} ({len(plan.entries)} entries)",
        file=sys.stderr,
    )
    return 0


def run(a: argparse.Namespace) -> int:
    return {"validate": _validate, "list": _list, "add": _add, "ack": _ack}[a.changes_cmd](a)
