"""What bridge 1.1 adds to ``profile``, ``diff``, ``check`` and ``verify``.

The 1.0 handlers (``handlers/flow.py``) are left as they are. 1.1 wraps them: a request served as
1.0 is answered by the 1.0 handler itself, so its response is the 1.0 response; a request served
as 1.1 gets the same result plus what 1.1 adds:

* ``project`` (a ``shape.yml`` path) on all four, and ``source`` (a source name) on ``diff`` and
  ``check``: the settings of the project apply as for ``--project FILE --source NAME``.
  ``profile``'s ``source`` and ``verify``'s ``path`` may name a source of the project instead of a
  path. On ``verify`` ``source`` is the real data (below), so a source is selected as the command
  line does: a single-source project selects it, else ``path`` names it.
* ``verify``'s ``source`` (the real data, read with the same ``format`` as ``path``): runs the
  memorization gate and, when the configuration has a utility section, the utility gate, with the
  pass results and exit semantics of ``shape verify --source``.
* ``verify``'s gates carry ``details``: counts, rates, distances and scores, never a data value.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from shape.bridge.context import Context
from shape.bridge.handlers import flow
from shape.bridge.handlers.common import ANY, BOOL, STR, arr, jsonable, mapping, obj, or_spilled
from shape.bridge.handlers.project import PROJECT_ARG, PROJECT_BLOCK, select, source_named_by
from shape.bridge.protocol import BridgeError
from shape.bridge.spec import Arg, Command

#: Details of a gate that are values of the data (the extremes the range gate saw), not counts,
#: rates, distances or scores.
_VALUE_DETAILS = {"range_constraint": frozenset({"actual_min", "actual_max"})}


def safe_details(gate: str, details: Any) -> Any:
    """A gate's ``details`` without data values: the keys of :data:`_VALUE_DETAILS` are dropped
    at any depth; counts, rates, distances, scores and names stay."""
    drop = _VALUE_DETAILS.get(gate, frozenset())

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            return {str(k): walk(v) for k, v in node.items() if k not in drop}
        if isinstance(node, list | tuple):
            return [walk(v) for v in node]
        return node

    return jsonable(walk(details))


# ---- profile ------------------------------------------------------------------------------


def profile_11(args: dict[str, Any], ctx: Context) -> dict[str, Any]:
    if ctx.minor < 1 or "project" not in args:
        return flow.cmd_profile(args, ctx)
    pc = select(args, ctx)
    named = source_named_by(str(args["source"]), pc)
    if named is not None:  # `source: "orders"`: the source of the project, not a path
        args = {
            **args,
            "source": named.path,
            "dataset": bool(args.get("dataset")) or named.dataset,
            "name": args.get("name") or named.name,
        }
    return flow.cmd_profile({k: v for k, v in args.items() if k != "project"}, ctx)


# ---- diff ---------------------------------------------------------------------------------


def diff_11(args: dict[str, Any], ctx: Context) -> dict[str, Any]:
    if ctx.minor < 1 or not ("project" in args or "source" in args):
        return flow.cmd_diff(args, ctx)
    import shape
    from shape.cli import project as project_cli

    before, after = flow._load_profile(args["before"], ctx), flow._load_profile(args["after"], ctx)
    pc = select(args, ctx, named=args.get("source"), hint=after.name)
    assert pc is not None
    options = project_cli.merge_diff_options(
        pc.source,
        {
            "thresholds": args.get("thresholds") or None,
            "column_thresholds": args.get("column_thresholds") or None,
            "ignore_columns": args.get("ignore_columns") or None,
            "only_columns": args.get("only_columns") or None,
            "policy": args.get("policy"),
        },
        args.get("ignore_columns") is not None,
    )
    result = shape.diff(before, after, **options)
    doc = jsonable(result.to_dict())
    changes = flow.served_changes(doc.pop("changes", []), ctx)
    if not ctx.include_raw:
        classified = flow.classified_columns(before) | flow.classified_columns(after)
        changes = flow.redact_entries(changes, classified)
    changes = [project_cli.annotate(pc.source, c) for c in changes]
    return {
        "drifted": bool(doc.pop("drifted", result.drifted)),
        "change_count": len(changes),
        "changes": ctx.spill("the changes", changes),
        **doc,
        "project": jsonable(pc.block()),
    }


# ---- check --------------------------------------------------------------------------------


def check_11(args: dict[str, Any], ctx: Context) -> dict[str, Any]:
    if ctx.minor < 1 or not ("project" in args or "source" in args):
        return flow.cmd_check(args, ctx)
    import shape
    from shape.cli import project as project_cli

    prof = flow._load_profile(args["profile"], ctx)
    pc = select(args, ctx, named=args.get("source"), hint=prof.name)
    assert pc is not None
    result = shape.check(prof, args["contract"])
    doc = jsonable(result.to_dict())
    violations = doc.pop("violations", [])
    if not ctx.include_raw:
        violations = flow.redact_entries(violations, flow.classified_columns(prof))
    violations = [project_cli.annotate(pc.source, v) for v in violations]
    return {
        "passed": bool(doc.pop("passed", result.passed)),
        "violation_count": len(violations),
        "violations": ctx.spill("the violations", violations),
        **doc,
        "project": jsonable(pc.block()),
    }


# ---- verify -------------------------------------------------------------------------------


def verify_11(args: dict[str, Any], ctx: Context) -> dict[str, Any]:
    if ctx.minor < 1:  # a request served as 1.0 gets 1.0's gates: no details
        return flow.cmd_verify(args, ctx)
    from shape.quality import VerifyRunner, load_gate_schema, load_tables, load_verify_config
    from shape.quality.verify import data_files

    pc = select(args, ctx)
    path = str(args["path"])
    named = source_named_by(path, pc)
    if named is not None:  # `path: "orders"`: the source of the project, not a path
        path = named.path
    fmt = args.get("format", "auto")
    tables = load_tables(path, fmt)
    if not tables:
        raise BridgeError("input.invalid_value", f"no {fmt} data files found in {path}")
    schema_path, config_path = args.get("schema"), args.get("config")
    schema = load_gate_schema(schema_path) if schema_path else None
    config = load_verify_config(config_path) if config_path else None
    source_arg = args.get("source")
    if config is not None and config.needs_source and not source_arg:
        raise BridgeError(
            "input.invalid_value",
            "the verify configuration asks for the memorization or utility gate, which compare "
            "with the source data: give --source",
        )
    source = load_tables(str(source_arg), fmt) if source_arg else None
    if source_arg and not source:
        raise BridgeError("input.invalid_value", f"no {fmt} data files found in {source_arg}")
    result = VerifyRunner(
        schema,
        bool(args.get("statistical")),
        path,
        schema_path,
        config,
        config_path,
        data_files(path, fmt),
        source=source,
        source_path=str(source_arg) if source_arg else None,
    ).run(tables)

    def enforced(gate: Any) -> bool:
        return pc is None or pc.project.gate_mode(gate.gate_name) == "enforce"

    gates = []
    for g in result.gate_results:
        entry = {
            "name": g.gate_name,
            "passed": bool(g.passed),
            "errors": [str(e) for e in g.errors],
            "warnings": [str(w) for w in g.warnings],
            "details": safe_details(g.gate_name, g.details),
        }
        if pc is not None:
            entry["mode"] = pc.project.gate_mode(g.gate_name)
        gates.append(entry)
    # `passed` is the 1.0 result: every gate passed (and, with `strict`, none warned). Under a
    # project, `enforced_passed` is the CLI's: every gate the project enforces passed; the exit
    # code of `shape verify` is 0 when it holds and, with `strict`, no enforced gate warned.
    has_warnings = any(g["warnings"] for g in gates)
    enforced_passed = all(g.passed for g in result.gate_results if enforced(g))
    out: dict[str, Any] = {
        "passed": bool(result.passed) and not (args.get("strict") and has_warnings),
        "gates": ctx.spill("the gates", gates),
        "row_counts": {n: int(c) for n, c in sorted(result.row_counts.items())},
        "statistical": bool(args.get("statistical")),
    }
    if pc is not None:
        out["enforced_passed"] = enforced_passed
        out["project"] = jsonable(pc.block())
    return out


# ---- the table ----------------------------------------------------------------------------

_SOURCE_NAME = Arg(
    "string",
    "the name of a source of `project`: its path, contract, baseline, thresholds, ignore lists "
    "and owners apply (needs project)",
    since="1.1",
)
_GATE = obj(
    {"name": STR, "passed": BOOL, "errors": arr(STR), "warnings": arr(STR)},
    {"details": mapping(ANY), "mode": {"enum": ["observe", "enforce"]}},
)

#: command -> (handler, arguments added in 1.1, optional result fields added in 1.1)
_EXTENSIONS: dict[str, tuple[Any, dict[str, Arg], dict[str, Any]]] = {
    "profile": (
        profile_11,
        {"project": PROJECT_ARG},
        {},
    ),
    "diff": (
        diff_11,
        {"project": PROJECT_ARG, "source": _SOURCE_NAME},
        {"project": PROJECT_BLOCK},
    ),
    "check": (
        check_11,
        {"project": PROJECT_ARG, "source": _SOURCE_NAME},
        {"project": PROJECT_BLOCK},
    ),
    "verify": (
        verify_11,
        {
            "project": PROJECT_ARG,
            "source": Arg(
                "string",
                "the real data the data was made from (a file or directory, read with the same "
                "`format` as `path`): runs the memorization gate, and the utility gate when the "
                "configuration has a utility section",
                path="read",
                since="1.1",
            ),
        },
        {"enforced_passed": BOOL, "project": PROJECT_BLOCK},
    ),
}


def extend(commands: dict[str, Command]) -> dict[str, Command]:
    """``commands`` with the 1.1 handlers, arguments and result fields of the four commands."""
    out = dict(commands)
    for name, (handler, extra_args, extra_result) in _EXTENSIONS.items():
        command = out[name]
        result = dict(command.result)
        if name == "verify":
            result["properties"] = {
                **result["properties"],
                "gates": or_spilled(arr(_GATE)),
            }
        if extra_result:
            result["properties"] = {**result["properties"], **extra_result}
        out[name] = replace(
            command, handler=handler, args={**command.args, **extra_args}, result=result
        )
    return out
