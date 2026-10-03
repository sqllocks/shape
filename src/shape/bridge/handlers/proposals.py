"""``proposals_propose``, ``proposals_list`` and ``proposals_decide`` (bridge 1.1).

They call what ``shape proposals propose|list|decide`` call (``shape.proposals``) and read and
write the same decision file. A decision file is the person's record, so these commands never
decide on their own: ``proposals_propose`` accepts nothing unless ``auto_accept`` is given.

Safe by default: the evidence of a proposal can hold values of the profiled columns (the range of
a relationship's child and parent columns). The decision file does not say which columns are
classified, so the values in evidence are withheld whatever the column: ``null`` with
``"redacted": true`` on the proposal, unless the request sets ``options.include_raw_values``.
Counts, fractions and names stay.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from shape.bridge.context import Context
from shape.bridge.errors import writing
from shape.bridge.handlers.common import (
    ANY,
    BOOL,
    INT,
    NUM,
    STR,
    STRS,
    arr,
    mapping,
    nullable,
    obj,
    or_spilled,
)
from shape.bridge.handlers.flow import _load_profile
from shape.bridge.protocol import BridgeError
from shape.bridge.spec import Arg, Command

_KINDS = (
    "relationship",
    "pii",
    "semantic",
    "rule",
)  # shape.proposals.KINDS; a test keeps them equal
_DEFAULT_KINDS = _KINDS[:3]  # shape.proposals.DEFAULT_KINDS: what a run proposes unless asked
_STATUSES = ("pending", "accepted", "rejected", "deferred", "stale")
#: What 1.2 added to the enumerations above (a request served as 1.1 does not know them).
_KINDS_SINCE = {"rule": "1.2"}
_STATUSES_SINCE = {"stale": "1.2"}
_VERBS = ("accept", "reject", "defer")
_STATUS_OF = {"accept": "accepted", "reject": "rejected", "defer": "deferred"}


def read_decisions(path: str, minor: int = 2) -> Any:
    """The decision file at ``path``, with the errors of the bridge: ``input.not_found``,
    ``input.invalid_schema`` for a file that is not a decision file, and
    ``input.unsupported_format_version`` for one a newer Shape wrote. A request served as 1.1
    reads version 1 only, as 1.1 did (version 2 holds rule proposals, which 1.2 added)."""
    import shape.proposals as engine
    from shape.proposals import FORMAT, DecisionError, DecisionFile

    VERSION = engine.MAX_VERSION if minor >= 2 else engine.VERSION  # noqa: N806

    text = Path(path).read_text(encoding="utf-8")  # a missing file is input.not_found
    try:
        doc = json.loads(text)
    except ValueError as exc:
        raise BridgeError("input.invalid_schema", f"{path}: not valid JSON: {exc}") from exc
    version = doc.get("version") if isinstance(doc, dict) else None
    if (
        isinstance(doc, dict)
        and doc.get("format") == FORMAT
        and isinstance(version, int)
        and not isinstance(version, bool)
        and version > VERSION
    ):
        raise BridgeError(
            "input.unsupported_format_version",
            f"{path} is a decision file of version {version}, which is newer than the version "
            f"{VERSION} this Shape reads",
            "upgrade Shape to read it",
        )
    try:
        return DecisionFile.from_dict(doc)
    except DecisionError as exc:
        raise BridgeError("input.invalid_schema", f"{path}: {exc}") from exc


def _scalars(value: Any) -> bool:
    return bool(value) and all(
        isinstance(v, str | int | float) and not isinstance(v, bool) for v in value
    )


def redact_evidence(evidence: Any) -> tuple[Any, bool]:
    """``evidence`` without the values it holds, and whether anything was withheld.

    Denied by shape, not by name: a ``range`` has its ``child`` and ``parent`` endpoints
    withheld, and so does any other list of plain values (a sample, an example list), wherever it
    sits. Booleans, counts, fractions and text such as a rule's description stay."""
    withheld = False

    def walk(node: Any, key: str | None = None) -> Any:
        nonlocal withheld
        if isinstance(node, dict):
            out: dict[str, Any] = {}
            for k, v in node.items():
                if key == "range" and k in ("child", "parent") and v is not None:
                    withheld = True
                    out[k] = None
                else:
                    out[k] = walk(v, k)
            return out
        if isinstance(node, list):
            if _scalars(node):
                withheld = True
                return None
            return [walk(v, key) for v in node]
        return node

    return walk(evidence), withheld


def entry_dict(entry: Any, ctx: Context) -> dict[str, Any]:
    """A proposal and the decision on it, as ``proposals_list`` returns it."""
    p, d = entry.proposal, entry.decision
    evidence, redacted = (p.evidence, False) if ctx.include_raw else redact_evidence(p.evidence)
    out: dict[str, Any] = {
        "id": p.id,
        "kind": p.kind,
        "subject": p.subject,
        "claim": p.claim,
        "confidence": p.confidence,
        "evidence": evidence,
        "proposed_at": p.proposed_at,
        "decision": {
            "status": entry.status,
            "actor": d.actor if d else None,
            "note": d.note if d else None,
            "decided_at": d.at if d else None,
        },
    }
    if redacted:
        out["redacted"] = True
    return out


def _require_file(path: str, what: str) -> None:
    if not Path(path).is_file():
        raise BridgeError("input.not_found", f"{what} not found: {path}")


def _data(items: list[str] | None) -> Any:
    """``data`` as ``shape proposals propose --data`` takes it: one directory, or NAME=PATH
    pairs. Every path must exist."""
    if not items:
        return None
    from shape.cli.proposals import _data_arg

    try:
        data = _data_arg(list(items))
    except ValueError as exc:
        raise BridgeError("usage.invalid_argument", f"data: {exc}") from exc
    for path in data.values() if isinstance(data, dict) else [data]:
        if not Path(path).exists():
            raise BridgeError("input.not_found", f"data not found: {path}")
    return data


# ---- proposals_propose --------------------------------------------------------------------


def prepare_propose(args: dict[str, Any], ctx: Context) -> None:
    _require_file(str(args["profile"]), "profile")
    _data(args.get("data"))
    decisions = str(args["decisions"])
    if Path(decisions).exists():
        read_decisions(decisions, ctx.minor)


def cmd_propose(args: dict[str, Any], ctx: Context) -> dict[str, Any]:
    from shape.proposals import DecisionFile, propose

    profile = _load_profile(str(args["profile"]), ctx)
    data = _data(args.get("data"))
    kinds = list(args.get("kinds") or _DEFAULT_KINDS)
    decisions = str(args["decisions"])
    file = (
        read_decisions(decisions, ctx.minor) if Path(decisions).exists() else DecisionFile.empty()
    )
    with_rules = "rule" in kinds  # only a request served as 1.2 can ask for it
    found = propose(
        profile,
        data,
        kinds=kinds,
        min_confidence=float(args.get("min_confidence", 0.5)),
        **({"decisions": file} if with_rules else {}),
    )
    result = file.update(found, kinds=kinds, auto_accept=args.get("auto_accept"))
    with writing():
        file.write(decisions)
    changed = {*result.added, *result.updated, *result.skipped_rejected}
    return {
        **({"stale": list(result.stale)} if with_rules else {}),
        "decisions": decisions,
        "proposals": len(found),
        "proposal_ids": [p.id for p in found],
        "added": len(result.added),
        "unchanged": len([p for p in found if p.id not in changed]),
        "skipped": list(result.skipped_rejected),
        "updated": len(result.updated),
        "withdrawn": len(result.withdrawn),
        "skipped_rejected": list(result.skipped_rejected),
        "auto_accepted": list(result.auto_accepted),
    }


# ---- proposals_list -----------------------------------------------------------------------


def cmd_list(args: dict[str, Any], ctx: Context) -> dict[str, Any]:
    file = read_decisions(str(args["decisions"]), ctx.minor)
    rows = file.list(
        status=args.get("status"), kind=args.get("kind"), min_confidence=args.get("min_confidence")
    )
    return {
        "decisions": str(args["decisions"]),
        "count": len(rows),
        "proposals": ctx.spill("the proposals", [entry_dict(r, ctx) for r in rows]),
    }


# ---- proposals_decide ---------------------------------------------------------------------


def cmd_decide(args: dict[str, Any], ctx: Context) -> dict[str, Any]:
    from shape.cli.proposals import _actor
    from shape.proposals import Entry

    path = str(args["decisions"])
    file = read_decisions(path, ctx.minor)
    proposal_id = str(args["proposal"])
    known = {e.proposal.id for e in file.entries()}
    if proposal_id not in known:
        raise BridgeError(
            "input.unknown_proposal",
            f"no proposal {proposal_id!r} in {path}",
            "run `proposals_list` for the ids",
        )
    decision = file.decide(
        proposal_id,
        _STATUS_OF[args["verb"]],
        actor=_actor(args.get("actor")),
        note=str(args.get("note", "")),
    )
    with writing():
        file.write(path)
    proposal = next(e.proposal for e in file.entries() if e.proposal.id == proposal_id)
    return entry_dict(Entry(proposal, decision), ctx)


# ---- proposals_contract -------------------------------------------------------------------


def prepare_contract(args: dict[str, Any], ctx: Context) -> None:
    _require_file(str(args["decisions"]), "decision file")
    if args.get("merge"):
        _require_file(str(args["merge"]), "contract to merge into")


def cmd_contract(args: dict[str, Any], ctx: Context) -> dict[str, Any]:
    """What ``shape proposals contract`` does: the accepted, non-stale rule proposals as a
    contract v1, added to ``merge`` when given. An accepted rule that disagrees with one there is
    ``input.contract_conflict``, which names both."""
    from shape.proposals import dump_contract

    file = read_decisions(str(args["decisions"]), ctx.minor)
    existing = None
    merge = args.get("merge")
    if merge:
        text = Path(str(merge)).read_text(encoding="utf-8")
        try:
            existing = json.loads(text)
        except ValueError as exc:
            raise BridgeError("input.invalid_schema", f"{merge} is not valid JSON: {exc}") from exc
    contract = file.to_contract(
        existing, merge_source=str(merge) if merge else "the existing contract"
    )
    output = str(args["output"])
    with writing():
        Path(output).write_bytes(dump_contract(contract).encode("utf-8"))
    return {"written": output, "rules": len(file.accepted("rule"))}


# ---- schemas ------------------------------------------------------------------------------

_DECISION = obj(
    {
        "status": {"enum": list(_STATUSES)},
        "actor": nullable(STR),
        "note": nullable(STR),
        "decided_at": nullable(STR),
    }
)
_PROPOSAL = obj(
    {
        "id": STR,
        "kind": {"enum": list(_KINDS)},
        "claim": mapping(ANY),
        "confidence": NUM,
        "evidence": nullable(mapping(ANY)),
        "decision": _DECISION,
    },
    {"subject": STR, "proposed_at": nullable(STR), "redacted": BOOL},
)

_DECISIONS_ARG = "the decision file (format shape-decisions)"

COMMANDS = [
    Command(
        "proposals_propose",
        "Find proposals for a profile and merge them into a decision file.",
        {
            "profile": Arg("string", "the .shape profile to propose for", True, path="read"),
            "decisions": Arg(
                "string",
                f"{_DECISIONS_ARG}; created when it does not exist, otherwise updated",
                True,
                path="write",
            ),
            "data": Arg(
                "array",
                "the profiled data, for value evidence: one directory with a NAME.csv or "
                "NAME.parquet per table, or NAME=PATH pairs",
                items="string",
                path="read",
            ),
            "kinds": Arg(
                "array",
                "the kinds to propose (default: relationship, pii and semantic; `rule`, added in "
                "1.2, proposes contract rules)",
                items="string",
                items_enum=_KINDS,
                enum_since=_KINDS_SINCE,
            ),
            "min_confidence": Arg(
                "number", "the lowest confidence proposed (default 0.5)", minimum=0, maximum=1
            ),
            "auto_accept": Arg(
                "number",
                "accept undecided proposals at or above this confidence, as actor `auto-accept` "
                "(off unless given; a decision already made is never overridden)",
                minimum=0,
                maximum=1,
            ),
        },
        obj(
            {
                "decisions": STR,
                "proposal_ids": STRS,
                "added": INT,
                "unchanged": INT,
                "skipped": STRS,
            },
            {
                "proposals": INT,
                "updated": INT,
                "withdrawn": INT,
                "skipped_rejected": STRS,
                "auto_accepted": STRS,
                "stale": STRS,
            },
        ),
        cmd_propose,
        job=True,
        prepare=prepare_propose,
        since="1.1",
        effects=("reads_files", "writes_files"),
    ),
    Command(
        "proposals_list",
        "List the proposals of a decision file and the decisions on them.",
        {
            "decisions": Arg("string", _DECISIONS_ARG, True, path="read"),
            "status": Arg(
                "string", "only proposals in this state", enum=_STATUSES, enum_since=_STATUSES_SINCE
            ),
            "kind": Arg(
                "string", "only proposals of this kind", enum=_KINDS, enum_since=_KINDS_SINCE
            ),
            "min_confidence": Arg(
                "number", "only proposals at or above this", minimum=0, maximum=1
            ),
        },
        obj({"count": INT, "proposals": or_spilled(arr(_PROPOSAL))}, {"decisions": STR}),
        cmd_list,
        since="1.1",
        effects=("reads_files",),
    ),
    Command(
        "proposals_decide",
        "Record that a proposal is accepted, rejected or deferred.",
        {
            "decisions": Arg("string", _DECISIONS_ARG, True, path="write"),
            "proposal": Arg("string", "the proposal id, such as `pii:customers.email`", True),
            "verb": Arg("string", "the decision", True, enum=_VERBS),
            "actor": Arg("string", "who decides (default: $SHAPE_ACTOR, else the login name)"),
            "note": Arg("string", "why"),
        },
        _PROPOSAL,
        cmd_decide,
        since="1.1",
        effects=("reads_files", "writes_files"),
    ),
    Command(
        "proposals_contract",
        "Write the accepted rule proposals of a decision file as a contract that `check` reads.",
        {
            "decisions": Arg("string", _DECISIONS_ARG, True, path="read"),
            "output": Arg(
                "string", "the contract file to write (format contract v1)", True, path="write"
            ),
            "merge": Arg(
                "string",
                "an existing contract file to add the rules to (left unchanged); an accepted rule "
                "that disagrees with one there is `input.contract_conflict`",
                path="read",
            ),
        },
        obj({"written": STR, "rules": INT}),
        cmd_contract,
        prepare=prepare_contract,
        since="1.2",
        effects=("reads_files", "writes_files"),
    ),
]
