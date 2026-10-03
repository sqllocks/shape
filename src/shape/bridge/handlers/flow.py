"""``profile``, ``diff``, ``check`` and ``verify``: the core workflow on real data, so a client
can run it without parsing CLI text. They call what ``shape profile|diff|check|verify`` call.

These handlers read real data, so they are safe by default: a column the safe-profile gate
classifies (a personal-data pattern, or nearly every value distinct) has its raw values withheld
from the result unless the request sets ``options.include_raw_values``. Withheld values are
``null`` and the entry says ``"redacted": true``. The ``.shape`` file ``profile`` writes is the
full profile: it is written where the caller says, and it holds real values."""

from __future__ import annotations

import argparse
import os
import re
from pathlib import Path
from typing import Any

from shape.bridge.context import Context
from shape.bridge.errors import writing
from shape.bridge.handlers.common import (
    ANY,
    BOOL,
    INT,
    STR,
    STRS,
    arr,
    jsonable,
    mapping,
    nullable,
    obj,
    or_spilled,
)
from shape.bridge.protocol import BridgeError
from shape.bridge.spec import Arg, Command

_RAW_SUMMARY_FIELDS = ("min", "max")
#: The data values a gate message may quote (the range gate's actual extremes).
_RAW_IN_MESSAGE = re.compile(r"\(actual (min|max): [^)]*\)")
_RAW_ENTRY_FIELDS = ("baseline", "current", "observed", "message", "detail")
#: What separates the columns in the label of an entry about several (the joint analysis):
#: ``a -> b``, ``a, b -> c``, ``a ~ b``, ``(a, b) in ref``, ``a='x' => b='y'``.
_LABEL_SPLIT = re.compile(r"\s*(?:->|=>|~|,|\bin\b|[()])\s*")


def classified_columns(profile: Any) -> set[str]:
    """The columns of ``profile`` whose raw values must not leave by default, named ``column`` and
    ``table.column``. The rule is the safe profile's own (``pii_gate_fires``)."""
    from shape.privacy.safe_profile import SafeConfig, pii_gate_fires

    config = SafeConfig()
    names: set[str] = set()
    for tname, table in profile.tables.items():
        rows = table["row_count"]
        for cname, col in table["columns"].items():
            if pii_gate_fires(col.get("pattern"), int(col.get("cardinality") or 0), rows, config):
                names.update((cname, f"{tname}.{cname}"))
    return names


def redact_summary(summary: dict[str, Any], classified: set[str]) -> dict[str, Any]:
    """``profile.summary()`` with the raw ``min`` and ``max`` of classified columns withheld."""

    def table(t: dict[str, Any], tname: str) -> dict[str, Any]:
        cols = {}
        for cname, col in t["columns"].items():
            if cname in classified or f"{tname}.{cname}" in classified:
                col = {**col, **{f: None for f in _RAW_SUMMARY_FIELDS}, "redacted": True}
            cols[cname] = col
        return {**t, "columns": cols}

    if "tables" in summary:
        return {**summary, "tables": {n: table(t, n) for n, t in summary["tables"].items()}}
    return table(summary, str(summary.get("name")))


def entry_columns(entry: dict[str, Any]) -> set[str]:
    """The column names a diff change or check violation is about: its ``column``, every name in
    the label of a joint entry (``"t.a -> b"`` gives ``t.a`` and ``b``), and the ``determinant``,
    ``dependent`` and ``columns`` of its ``detail``. Over-reading a label only redacts more."""
    names: set[str] = set()
    column = entry.get("column")
    if isinstance(column, str):
        names.add(column)
        for part in _LABEL_SPLIT.split(column):
            part = part.split("=", 1)[0].strip()
            if part:
                names.add(part)
    detail = entry.get("detail")
    if isinstance(detail, dict):
        for key in ("determinant", "columns"):
            if isinstance(detail.get(key), list):
                names.update(str(c) for c in detail[key])
        if isinstance(detail.get("dependent"), str):
            names.add(detail["dependent"])
    return names


def redact_entries(entries: list[dict[str, Any]], classified: set[str]) -> list[dict[str, Any]]:
    """Diff changes or check violations about a classified column, with the observed values
    (``baseline`` and ``current`` of a change, ``observed`` of a violation, and the ``message``
    and ``detail`` of a joint change, which quote values) withheld. An entry about several
    columns is withheld when any of them is classified."""
    out = []
    for entry in entries:
        if entry_columns(entry) & classified:
            entry = {
                **entry,
                **{f: None for f in _RAW_ENTRY_FIELDS if f in entry},
                "redacted": True,
            }
        out.append(entry)
    return out


def _load_profile(path: str, ctx: Context) -> Any:
    """Load a ``.shape`` profile. The notice that it is not signed is a warning of the response."""
    import warnings

    import shape

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        loaded = shape.load(path)
    for item in caught:
        if item.category.__name__ == "ArtifactNotVerifiedWarning":
            ctx.warn("artifact_not_verified", str(item.message))
        else:
            warnings.showwarning(item.message, item.category, item.filename, item.lineno)
    return loaded


# ---- profile ------------------------------------------------------------------------------


def cmd_profile(args: dict[str, Any], ctx: Context) -> dict[str, Any]:
    import shape
    from shape.cli.main import _profile_source

    source = _profile_source(  # type: ignore[no-untyped-call]
        argparse.Namespace(src=args["source"], dataset=bool(args.get("dataset")))
    )
    prof = shape.profile(
        source, name=args.get("name"), version=args.get("version"), as_of=args.get("as_of")
    )
    empty = [n for n, t in prof.tables.items() if not t["row_count"]]
    if empty:
        message = (
            f"{args['source']} has 0 rows in table(s) {', '.join(empty)}: the profile holds no "
            "data, and a diff against it is meaningless"
        )
        if args.get("fail_on_empty"):
            raise BridgeError("input.invalid_value", message)
        ctx.warn("empty_table", message)
    output = args.get("output")
    with writing():
        if output:
            target = Path(output)
            target.parent.mkdir(parents=True, exist_ok=True)
            content_id = shape.save(prof, str(target))
        else:
            folder = ctx.jobs_dir / "bridge" / "profiles"
            folder.mkdir(parents=True, exist_ok=True, mode=0o700)
            scratch = folder / f".new-{os.getpid()}.shape"
            content_id = shape.save(prof, str(scratch))
            target = folder / f"{str(content_id).replace(':', '-')}.shape"
            os.replace(scratch, target)
    ctx.warn(
        "profile_file_holds_values",
        f"{target} is the full profile: it holds real values (value counts and extremes); "
        "keep it private or share a safe profile",
    )
    summary = prof.summary()
    if not ctx.include_raw:
        summary = redact_summary(summary, classified_columns(prof))
    result: dict[str, Any] = {
        "path": str(target),
        "content_id": str(content_id),
        "name": prof.name,
        "tables": {
            name: {"rows": t["row_count"], "columns": len(t["columns"])}
            for name, t in prof.tables.items()
        },
        "summary": ctx.spill("the profile summary", jsonable(summary)),
    }
    if prof.provenance is not None:
        result["provenance"] = jsonable(prof.provenance)
    return result


# ---- diff ---------------------------------------------------------------------------------


def cmd_diff(args: dict[str, Any], ctx: Context) -> dict[str, Any]:
    import shape

    before, after = _load_profile(args["before"], ctx), _load_profile(args["after"], ctx)
    kwargs: dict[str, Any] = {
        "thresholds": args.get("thresholds") or None,
        "column_thresholds": args.get("column_thresholds") or None,
        "ignore_columns": args.get("ignore_columns") or None,
        "only_columns": args.get("only_columns") or None,
        "policy": args.get("policy"),
    }
    result = shape.diff(before, after, **kwargs)
    doc = jsonable(result.to_dict())
    if not ctx.include_raw:
        classified = classified_columns(before) | classified_columns(after)
        doc["changes"] = redact_entries(doc.get("changes", []), classified)
    changes = doc.pop("changes", [])
    return {
        "drifted": bool(doc.pop("drifted", result.drifted)),
        "change_count": len(changes),
        "changes": ctx.spill("the changes", changes),
        **{k: v for k, v in doc.items()},
    }


# ---- check --------------------------------------------------------------------------------


def cmd_check(args: dict[str, Any], ctx: Context) -> dict[str, Any]:
    import shape

    prof = _load_profile(args["profile"], ctx)
    result = shape.check(prof, args["contract"])
    doc = jsonable(result.to_dict())
    violations = doc.pop("violations", [])
    if not ctx.include_raw:
        violations = redact_entries(violations, classified_columns(prof))
    return {
        "passed": bool(doc.pop("passed", result.passed)),
        "violation_count": len(violations),
        "violations": ctx.spill("the violations", violations),
        **doc,
    }


# ---- verify -------------------------------------------------------------------------------


def cmd_verify(args: dict[str, Any], ctx: Context) -> dict[str, Any]:
    from shape.quality import VerifyRunner, load_gate_schema, load_tables, load_verify_config
    from shape.quality.verify import data_files

    path = str(args["path"])
    fmt = args.get("format", "auto")
    tables = load_tables(path, fmt)
    if not tables:
        raise BridgeError("input.invalid_value", f"no {fmt} data files found in {path}")
    schema_path, config_path = args.get("schema"), args.get("config")
    schema = load_gate_schema(schema_path) if schema_path else None
    config = load_verify_config(config_path) if config_path else None
    result = VerifyRunner(
        schema,
        bool(args.get("statistical")),
        path,
        schema_path,
        config,
        config_path,
        data_files(path, fmt),
    ).run(tables)
    gates = [
        _gate(g.gate_name, g.passed, g.errors, g.warnings, ctx.include_raw)
        for g in result.gate_results
    ]
    has_warnings = any(g["warnings"] for g in gates)
    passed = bool(result.passed) and not (args.get("strict") and has_warnings)
    return {
        "passed": passed,
        "gates": ctx.spill("the gates", gates),
        "row_counts": {n: int(c) for n, c in sorted(result.row_counts.items())},
        "statistical": bool(args.get("statistical")),
    }


def _gate(
    name: str, passed: bool, errors: list[Any], warnings: list[Any], include_raw: bool
) -> dict[str, Any]:
    """A gate's result. Unless raw values are asked for, a data value a message quotes (the
    range gate's actual minimum or maximum) is withheld and the gate says ``"redacted": true``."""
    texts = {"errors": [str(e) for e in errors], "warnings": [str(w) for w in warnings]}
    redacted = False
    if not include_raw:
        for key, items in texts.items():
            withheld = [_RAW_IN_MESSAGE.sub(r"(actual \1 withheld)", m) for m in items]
            redacted = redacted or withheld != items
            texts[key] = withheld
    gate: dict[str, Any] = {"name": name, "passed": bool(passed), **texts}
    if redacted:
        gate["redacted"] = True
    return gate


_THRESHOLDS = Arg("object", "metric name to threshold, as for `shape diff --threshold`")
_CHANGE = obj({"column": nullable(STR), "kind": STR}, {"redacted": BOOL, "severity": STR})

COMMANDS = [
    Command(
        "profile",
        "Profile a file, folder, glob or Delta table into a .shape artifact.",
        {
            "source": Arg("string", "a file, folder, glob or Delta table", True),
            "output": Arg(
                "string", "where to write the .shape profile (default: the jobs directory)"
            ),
            "name": Arg("string", "the profile's name"),
            "dataset": Arg("boolean", "a folder of table files: one table per file"),
            "version": Arg("integer", "a Delta table: profile this version", minimum=0),
            "as_of": Arg(
                "string", "a Delta table: the newest version at or before this ISO-8601 time"
            ),
            "fail_on_empty": Arg("boolean", "fail instead of warning when a table has 0 rows"),
        },
        obj(
            {
                "path": STR,
                "content_id": STR,
                "name": STR,
                "tables": mapping(obj({"rows": INT, "columns": INT})),
                "summary": or_spilled(mapping(ANY)),
            },
            {"provenance": mapping(ANY)},
        ),
        cmd_profile,
        job=True,
    ),
    Command(
        "diff",
        "Compare two profiles and report the drift.",
        {
            "before": Arg("string", "the baseline .shape profile", True),
            "after": Arg("string", "the current .shape profile", True),
            "policy": Arg("string", "a named drift policy"),
            "thresholds": _THRESHOLDS,
            "column_thresholds": Arg("object", "column name to {metric: threshold}"),
            "ignore_columns": Arg("array", "columns to leave out", items="string"),
            "only_columns": Arg("array", "compare only these columns", items="string"),
        },
        obj(
            {
                "drifted": BOOL,
                "change_count": INT,
                "changes": or_spilled(arr(_CHANGE)),
            }
        ),
        cmd_diff,
    ),
    Command(
        "check",
        "Check a profile against a contract.",
        {
            "profile": Arg("string", "the .shape profile", True),
            "contract": Arg("string", "the contract JSON file", True),
        },
        obj(
            {
                "passed": BOOL,
                "violation_count": INT,
                "violations": or_spilled(
                    arr(obj({"rule": STR}, {"column": nullable(STR), "redacted": BOOL}))
                ),
            }
        ),
        cmd_check,
    ),
    Command(
        "verify",
        "Run the validation gates over data files.",
        {
            "path": Arg("string", "a data file or a directory of data files", True),
            "format": Arg(
                "string",
                "the data format (default: auto)",
                enum=("auto", "csv", "parquet", "jsonl"),
            ),
            "schema": Arg("string", "a gate schema (or a Shape model v2) file"),
            "config": Arg("string", "a verify configuration file"),
            "statistical": Arg("boolean", "add the KS and chi-squared tests"),
            "strict": Arg("boolean", "a warning fails the run"),
        },
        obj(
            {
                "passed": BOOL,
                "gates": or_spilled(
                    arr(obj({"name": STR, "passed": BOOL, "errors": STRS, "warnings": STRS}))
                ),
                "row_counts": mapping(INT),
            },
            {"statistical": BOOL},
        ),
        cmd_verify,
        job=True,
    ),
]
