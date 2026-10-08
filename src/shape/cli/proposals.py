"""``shape proposals propose|list|decide|contract``: proposals and decision files (W1-02, W3-02).

``propose`` profiles nothing: it reads a profile (and optionally the data it came from, for value
evidence) and merges what it finds into a decision file; with ``--kinds rule`` it reads several
profiles and proposes contract rules. ``list`` shows proposals and their decisions; ``decide``
records accept, reject or defer with the actor and a note; ``contract`` writes the accepted rules
as a contract v1. Nothing is accepted automatically unless ``propose --auto-accept THRESHOLD`` is
given. Nothing heavy loads at import time (T-18).
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from pathlib import Path
from typing import Any

# shape.proposals.KINDS and DEFAULT_KINDS; a test keeps them equal
_KINDS = ("relationship", "pii", "semantic", "rule", "type")
_DEFAULT_KINDS = ("relationship", "pii", "semantic")
_LIST_STATUSES = ("pending", "accepted", "rejected", "deferred", "stale")
_VERBS = {"accept": "accepted", "reject": "rejected", "defer": "deferred"}


def add_arguments(sub: Any) -> None:
    KINDS = _KINDS
    top = sub.add_parser(
        "proposals",
        help="proposals Shape cannot settle alone (foreign keys, PII, meaning) and your decisions",
        description="Shape proposes, with evidence and a confidence; you accept, reject or defer; "
        "the decision file keeps the answer. A rejected proposal is not proposed again. "
        "See docs/PROPOSALS.md.",
    )
    cmds = top.add_subparsers(dest="proposals_cmd", required=True)
    decisions = argparse.ArgumentParser(add_help=False)
    decisions.add_argument(
        "-d", "--decisions", required=True, metavar="DECISIONS.json", help="the decision file"
    )

    pr = cmds.add_parser(
        "propose",
        parents=[decisions],
        help="find proposals for a profile and merge them into the decision file",
    )
    pr.add_argument(
        "profiles",
        nargs="+",
        metavar="PROFILE.shape",
        help="the profile; with --kinds rule several (for example a week of daily captures)",
    )
    pr.add_argument(
        "--data",
        action="append",
        default=[],
        metavar="DIR|NAME=PATH",
        help="the profiled data, for value evidence: a directory with one NAME.csv or "
        "NAME.parquet per table, or NAME=PATH pairs (repeatable)",
    )
    pr.add_argument(
        "--kinds",
        default=",".join(_DEFAULT_KINDS),
        help=f"comma-separated kinds to propose, of {','.join(KINDS)} "
        f"(default {','.join(_DEFAULT_KINDS)}; rule proposes contract rules, type reviews "
        "column types)",
    )
    pr.add_argument("--min-confidence", type=float, default=0.5, metavar="C")
    pr.add_argument(
        "--auto-accept",
        type=float,
        metavar="THRESHOLD",
        help="accept undecided proposals at or above this confidence (off unless given)",
    )

    ls = cmds.add_parser("list", parents=[decisions], help="list proposals and their decisions")
    ls.add_argument("--status", choices=_LIST_STATUSES)
    ls.add_argument("--kind", choices=KINDS)
    ls.add_argument("--min-confidence", type=float, metavar="C")
    ls.add_argument("--json", action="store_true", help="print JSON")

    dc = cmds.add_parser("decide", parents=[decisions], help="accept, reject or defer a proposal")
    dc.add_argument("proposal", metavar="PROPOSAL_ID")
    dc.add_argument("verb", choices=sorted(_VERBS))
    dc.add_argument("--actor", help="who decides (default: $SHAPE_ACTOR, else the login name)")
    dc.add_argument("--note", default="", help="why")

    ct = cmds.add_parser(
        "contract",
        parents=[decisions],
        help="write the accepted rule proposals as a contract v1 that `shape check` reads",
    )
    ct.add_argument("-o", "--output", required=True, metavar="CONTRACT.json")
    ct.add_argument(
        "--merge",
        metavar="EXISTING.json",
        help="add the rules to this contract; exit 2 if an accepted rule conflicts with one there",
    )


def _data_arg(items: list[str]) -> Any:
    if not items:
        return None
    pairs = [i for i in items if "=" in i]
    if pairs and len(pairs) != len(items):
        raise ValueError("give --data as one directory, or as NAME=PATH pairs, not both")
    if pairs:
        out: dict[str, str] = {}
        for item in pairs:
            name, _, path = item.partition("=")
            if not name or not path:
                raise ValueError(f"--data {item!r}: expected NAME=PATH")
            out[name] = path
        return out
    if len(items) != 1:
        raise ValueError("give one --data directory, or NAME=PATH pairs")
    return items[0]


def _load_or_new(path: str, *, create: bool) -> Any:
    from shape.proposals import DecisionFile

    if Path(path).exists():
        return DecisionFile.read(path)
    if create:
        return DecisionFile.empty()
    raise FileNotFoundError(f"decision file not found: {path}")


def _actor(given: str | None) -> str:
    if given:
        return given
    env = os.environ.get("SHAPE_ACTOR")
    if env:
        return env
    try:
        return getpass.getuser()
    except Exception as exc:  # no login name in some containers
        raise ValueError("who decides? give --actor or set SHAPE_ACTOR") from exc


def run(a: argparse.Namespace) -> int:
    from shape.proposals import KINDS, DecisionError, propose

    cmd = a.proposals_cmd
    if cmd == "propose":
        import shape

        kinds = [k.strip() for k in a.kinds.split(",") if k.strip()]
        for k in kinds:
            if k not in KINDS:
                raise DecisionError(f"unknown kind {k!r}")
        if len(a.profiles) > 1 and kinds != ["rule"]:
            raise DecisionError("several profiles are for --kinds rule only")
        file = _load_or_new(a.decisions, create=True)
        loaded = [shape.load(p) for p in a.profiles]
        found = propose(
            loaded if len(loaded) > 1 else loaded[0],
            _data_arg(a.data),
            kinds=kinds,
            min_confidence=a.min_confidence,
            decisions=file,
        )
        result = file.update(found, kinds=kinds, auto_accept=a.auto_accept)
        file.write(a.decisions)
        json.dump(
            {"written": a.decisions, "proposals": len(found), **result.to_dict()},
            sys.stdout,
            indent=2,
        )
        print()
        return 0
    file = _load_or_new(a.decisions, create=False)
    if cmd == "contract":
        return _contract(a, file)
    if cmd == "list":
        rows = file.list(status=a.status, kind=a.kind, min_confidence=a.min_confidence)
        if a.json:
            json.dump([r.to_dict() for r in rows], sys.stdout, indent=2)
            print()
            return 0
        for r in rows:
            p = r.proposal
            who = f" by {r.decision.actor}" if r.decision else ""
            print(f"{p.confidence:.2f}  {r.status:<8}  {p.id}{who}")
        return 0
    # decide
    status = _VERBS[a.verb]
    d = file.decide(a.proposal, status, actor=_actor(a.actor), note=a.note)
    file.write(a.decisions)
    json.dump(d.to_dict(), sys.stdout, indent=2)
    print()
    return 0


def _contract(a: argparse.Namespace, file: Any) -> int:
    from shape.proposals import DecisionError, dump_contract

    existing = None
    if a.merge:
        try:
            existing = json.loads(Path(a.merge).read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise FileNotFoundError(f"contract to merge into not found: {a.merge}") from None
        except ValueError as exc:
            raise DecisionError(f"{a.merge} is not valid JSON: {exc}") from exc
    contract = file.to_contract(existing, merge_source=a.merge or "the existing contract")
    Path(a.output).write_bytes(dump_contract(contract).encode("utf-8"))
    rules = len(file.accepted("rule"))
    json.dump({"written": a.output, "rules": rules}, sys.stdout, indent=2)
    print()
    return 0


def load_decisions(path: str | None) -> Any:
    """The decision file named by ``--decisions``, or ``None`` when the option was not given."""
    if path is None:
        return None
    from shape.proposals import DecisionFile

    return DecisionFile.read(path)
