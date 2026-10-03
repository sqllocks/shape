"""``design`` and ``design_from_data`` (bridge 1.1).

They call what ``shape design`` and ``shape design --from-data`` call (``shape.design``) and are
read-only: the DDL and the derived tables come back in the result, nothing is written. For the
same input the ``ddl`` text is byte for byte the file ``shape design -o`` writes, and ``tables`` is
the document ``--json`` writes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from shape.bridge.context import Context
from shape.bridge.handlers.common import BOOL, INT, STR, arr, mapping, nullable, obj, or_spilled
from shape.bridge.protocol import BridgeError
from shape.bridge.spec import Arg, Command

_MODES = ("3nf", "star", "snowflake")  # shape.design.engine.MODES; a test keeps them equal
_DIALECTS = ("tsql", "tsql-fabric-warehouse", "postgres", "mysql")  # shape.design.ddl.DIALECTS


def read_design(path: str) -> Any:
    """The design input at ``path``, with the errors of the bridge: ``input.not_found``,
    ``input.invalid_schema``, and ``input.unsupported_format_version`` for a newer one."""
    from shape.design.model import DESIGN_FORMAT, DESIGN_VERSION, DesignError, load_design

    text = Path(path).read_text(encoding="utf-8")  # a missing file is input.not_found
    try:
        doc = json.loads(text)
    except ValueError as exc:
        raise BridgeError("input.invalid_schema", f"{path}: not valid JSON ({exc})") from exc
    version = doc.get("version") if isinstance(doc, dict) else None
    if (
        isinstance(doc, dict)
        and doc.get("format") == DESIGN_FORMAT
        and isinstance(version, int)
        and not isinstance(version, bool)
        and version > DESIGN_VERSION
    ):
        raise BridgeError(
            "input.unsupported_format_version",
            f"{path} is a design input of version {version}, which is newer than the version "
            f"{DESIGN_VERSION} this Shape reads",
            "upgrade Shape to read it",
        )
    try:
        return load_design(path)
    except DesignError as exc:
        raise BridgeError("input.invalid_schema", f"{path}: {exc}") from exc


def cmd_design(args: dict[str, Any], ctx: Context) -> dict[str, Any]:
    from shape.design.ddl import emit_ddl
    from shape.design.engine import derive
    from shape.design.lint import lint

    design = read_design(str(args["input"]))
    mode = args.get("mode", "3nf")
    findings = lint(design, mode)
    report = [f.to_dict() for f in findings]
    passed = not any(f.severity == "error" for f in findings)
    if not passed:  # as on the command line: no DDL for a design with errors
        return {"lint": report, "tables": None, "ddl": None, "passed": False}
    result = derive(design, mode)
    ddl = emit_ddl(
        result,
        args.get("dialect", "tsql"),
        schema_name=args.get("schema_name"),
        drop=bool(args.get("drop")),
    )
    return {
        "lint": report,
        "tables": result.to_dict(),
        "ddl": ctx.spill("the DDL", ddl),
        "passed": True,
    }


def cmd_from_data(args: dict[str, Any], ctx: Context) -> dict[str, Any]:
    from shape.design.from_data import design_from_rows
    from shape.io import iter_rows

    source = str(args["source"])
    name = args.get("name") or Path(source).stem
    design = design_from_rows(iter_rows(source), name=str(name))
    return design.to_dict()


_FINDING = obj(
    {"code": STR, "severity": {"enum": ["error", "warning", "info"]}, "path": STR, "message": STR}
)
_TABLE = obj({"name": STR, "kind": STR, "columns": arr(obj({"name": STR, "type": STR}))})

COMMANDS = [
    Command(
        "design",
        "Design a 3NF, star or snowflake schema from a design input: lint report, tables and DDL.",
        {
            "input": Arg(
                "string", "a design input file (JSON, format shape-design)", True, path="read"
            ),
            "mode": Arg("string", "the schema to derive (default 3nf)", enum=_MODES),
            "dialect": Arg("string", "the SQL dialect of the DDL (default tsql)", enum=_DIALECTS),
            "schema_name": Arg("string", "qualify every table with this schema"),
            "drop": Arg("boolean", "drop each table before creating it"),
        },
        obj(
            {
                "lint": arr(_FINDING),
                "tables": nullable(
                    obj(
                        {
                            "format": STR,
                            "version": INT,
                            "name": STR,
                            "mode": STR,
                            "tables": arr(_TABLE),
                        }
                    )
                ),
                "ddl": nullable(or_spilled(STR)),
                "passed": BOOL,
            }
        ),
        cmd_design,
        since="1.1",
        effects=("reads_files",),
    ),
    Command(
        "design_from_data",
        "Build a design input from a data file: its types, keys and exact functional dependencies.",
        {
            "source": Arg("string", "a data file (CSV, Parquet or JSONL)", True, path="read"),
            "name": Arg("string", "the design and entity name (default: the file's name)"),
        },
        obj({"format": STR, "version": INT, "name": STR, "entities": arr(mapping({}))}),
        cmd_from_data,
        since="1.1",
        effects=("reads_files",),
    ),
]
