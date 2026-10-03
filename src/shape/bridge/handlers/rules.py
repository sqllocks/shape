"""``rules_mutate`` and ``rules_backtest`` (bridge 1.2).

They call what ``shape rules mutate`` and ``shape rules backtest`` call (``shape.rules``) and return
the ``shape-mutation-report`` and ``shape-backtest-report`` documents, as the command line's
``--json`` prints them. A mutation report lists each mutant by its corruption kind, table and
column, with the rules that killed it: no value of the data. ``min_score`` adds the outcome of the
gate (``min_score``: ``required`` and ``met``) to the report; the report itself is unchanged.

``rules_mutate`` is cancellable between mutants: a cancelled job is ``cancelled`` and its result
counts the mutants run so far.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from shape.bridge.context import Context
from shape.bridge.handlers.common import (
    ANY,
    BOOL,
    INT,
    NUM,
    STR,
    arr,
    mapping,
    nullable,
    obj,
    or_spilled,
)
from shape.bridge.jobs import JobCancelled
from shape.bridge.protocol import BridgeError
from shape.bridge.spec import Arg, Command

_WINDOWS = ("day", "week", "month")


def _require(args: dict[str, Any], *names: str) -> None:
    for name in names:
        value = args.get(name)
        if value is not None and not Path(str(value)).exists():
            raise BridgeError("input.not_found", f"{name} not found: {value}")


def _check_version(path: Any, what: str, fmt: str, newest: int) -> None:
    """``input.unsupported_format_version`` for a file of ``fmt`` that a newer Shape wrote (the
    engine reads versions up to ``newest``); anything else is left to the engine to judge."""
    import json

    if path is None:
        return
    try:
        doc = json.loads(Path(str(path)).read_text(encoding="utf-8"))
    except ValueError:
        return
    version = doc.get("version") if isinstance(doc, dict) else None
    if (
        isinstance(doc, dict)
        and doc.get("format") == fmt
        and isinstance(version, int)
        and not isinstance(version, bool)
        and version > newest
    ):
        raise BridgeError(
            "input.unsupported_format_version",
            f"{path} is a {what} of version {version}, which is newer than the version {newest} "
            "this Shape reads",
            "upgrade Shape to read it",
        )


# ---- rules_mutate -------------------------------------------------------------------------


def prepare_mutate(args: dict[str, Any], ctx: Context) -> None:
    from shape.rules.mutation import PLAN_FORMAT, PLAN_VERSION

    _require(args, "data", "contract", "plan")
    _check_version(args.get("plan"), "mutation plan", PLAN_FORMAT, PLAN_VERSION)


def cmd_mutate(args: dict[str, Any], ctx: Context) -> Any:
    from shape.rules.mutation import MutationCancelled, MutationError, mutation_test

    prepare_mutate(args, ctx)

    kwargs: dict[str, Any] = {}
    if args.get("rate") is not None:
        kwargs["rate"] = float(args["rate"])
    try:
        result = mutation_test(
            str(args["data"]),
            str(args["contract"]),
            plan=args.get("plan"),
            seed=int(args.get("seed", 0)),
            should_stop=ctx.cancel.is_set,
            on_mutant=lambda done, total: ctx.progress(
                {"mutants_run": done, "mutants_total": total}
            ),
            **kwargs,
        )
    except MutationCancelled as stopped:
        mutants = stopped.result.mutants
        raise JobCancelled(
            {
                "mutants_run": len(mutants),
                "killed": sum(1 for m in mutants if m["status"] == "killed"),
                "survived": sum(1 for m in mutants if m["status"] == "survived"),
                "not_applicable": sum(1 for m in mutants if m["status"] == "not_applicable"),
            }
        ) from None
    report = result.to_dict()
    if report["score"]["overall"]["applicable"] == 0:
        raise MutationError("no mutant changed a cell: the data has nothing to corrupt")
    required = args.get("min_score")
    if required is not None:
        report["min_score"] = {
            "required": float(required),
            "met": (result.score or 0.0) >= float(required),
        }
    return ctx.spill("the mutation report", report)


# ---- rules_backtest -----------------------------------------------------------------------


def prepare_backtest(args: dict[str, Any], ctx: Context) -> None:
    from shape.rules.history import INCIDENTS_FORMAT, INCIDENTS_VERSION

    _require(args, "registry", "contract", "incidents", "compare")
    _check_version(args.get("incidents"), "incidents file", INCIDENTS_FORMAT, INCIDENTS_VERSION)
    if not (Path(str(args["registry"])) / "logs").is_dir():  # opening it would create one
        raise BridgeError(
            "input.invalid_value", f"{args['registry']} is not a registry: it has no logs folder"
        )


def cmd_backtest(args: dict[str, Any], ctx: Context) -> Any:
    from shape.rules.history import backtest

    prepare_backtest(args, ctx)
    result = backtest(
        str(args["registry"]),
        str(args["name"]),
        str(args["contract"]),
        since=args.get("since"),
        until=args.get("until"),
        window=str(args.get("window", "day")),
        incidents=args.get("incidents"),
        compare=args.get("compare"),
    )
    return ctx.spill("the backtest report", result.to_dict())


# ---- schemas ------------------------------------------------------------------------------

_SCORE = obj({"killed": INT, "applicable": INT, "score": nullable(NUM)})
_MUTATION = obj(
    {
        "format": {"const": "shape-mutation-report"},
        "version": INT,
        "seed": INT,
        "rate": NUM,
        "diff": BOOL,
        "mutants": arr(
            obj(
                {"id": STR, "kind": STR, "table": STR, "status": STR, "killed": BOOL},
                {"column": nullable(STR)},
            )
        ),
        "score": obj({"overall": _SCORE, "by_kind": mapping(_SCORE), "by_table": mapping(_SCORE)}),
        "rules": mapping(ANY),
        "rules_killed_none": arr(STR),
        "baseline_failed_rules": arr(STR),
    },
    {"min_score": obj({"required": NUM, "met": BOOL})},
)
_BACKTEST = obj(
    {
        "format": {"const": "shape-backtest-report"},
        "version": INT,
        "name": STR,
        "window": {"enum": list(_WINDOWS)},
        "summary": obj({"entries": INT, "pass": INT, "fail": INT, "not_measured": INT}),
        "entries": arr(obj({"id": STR, "status": {"enum": ["pass", "fail", "not_measured"]}})),
        "rules": mapping(ANY),
    },
    {
        "since": nullable(STR),
        "until": nullable(STR),
        "incidents": arr(ANY),
        "alarms_outside_incidents": arr(STR),
        "compare": obj({"disagreements": INT, "entries": arr(ANY)}),
    },
)

COMMANDS = [
    Command(
        "rules_mutate",
        "Plant known faults in data and report which rules of a contract catch them "
        "(job-capable, cancellable between mutants).",
        {
            "data": Arg(
                "string",
                "a data file, or a folder of files with one table each",
                True,
                path="read",
            ),
            "contract": Arg("string", "the contract file (format contract v1)", True, path="read"),
            "plan": Arg(
                "string",
                "a mutation plan file (format shape-mutation-plan); default: every applicable "
                "corruption of every table and column",
                path="read",
            ),
            "seed": Arg("integer", "the seed (default 0); the same seed gives the same report"),
            "rate": Arg(
                "number",
                "the share of rows each mutant changes (default 0.05)",
                minimum=0,
                maximum=1,
            ),
            "min_score": Arg(
                "number",
                "the mutation score to reach: the report then says whether it was met",
                minimum=0,
                maximum=1,
            ),
        },
        or_spilled(_MUTATION),
        cmd_mutate,
        job=True,
        cancellable=True,
        prepare=prepare_mutate,
        since="1.2",
        effects=("reads_files", "cancels"),
    ),
    Command(
        "rules_backtest",
        "Replay a contract over every committed version of a registry name (job-capable).",
        {
            "registry": Arg("string", "the registry folder", True, path="read"),
            "name": Arg("string", "the registry name", True),
            "contract": Arg("string", "the contract file (format contract v1)", True, path="read"),
            "since": Arg("string", "the first date, inclusive (YYYY-MM-DD)"),
            "until": Arg("string", "the last date, inclusive (YYYY-MM-DD)"),
            "window": Arg(
                "string",
                "one entry per `day` (a version), or per ISO `week` or `month` (merged)",
                enum=_WINDOWS,
            ),
            "incidents": Arg(
                "string", "a file of known incidents (format shape-incidents)", path="read"
            ),
            "compare": Arg(
                "string", "an older contract file to run beside `contract`", path="read"
            ),
        },
        or_spilled(_BACKTEST),
        cmd_backtest,
        job=True,
        prepare=prepare_backtest,
        since="1.2",
        effects=("reads_files",),
    ),
]
