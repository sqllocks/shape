"""``shape design``: a design input to 3NF, star or snowflake DDL, with a lint report; or a design
input built from data (``--from-data``). See ``docs/DESIGN.md``."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def add_parsers(sub: Any) -> None:
    d = sub.add_parser(
        "design",
        help="design a 3NF, star or snowflake schema from a design input (DDL and lint)",
        description="Reads a design input (JSON, format shape-design), lints it and writes DDL "
        "for the chosen mode and dialect. The same input always gives the same bytes. With "
        "--from-data the input is a data file, and the command writes a design input built from "
        "its types, keys and exact functional dependencies instead.",
    )
    d.add_argument(
        "input", metavar="INPUT", help="a design input .json (or a data file with --from-data)"
    )
    d.add_argument(
        "--mode", default="3nf", choices=("3nf", "star", "snowflake"), help="default 3nf"
    )
    d.add_argument(
        "--dialect",
        default="tsql",
        choices=("tsql", "tsql-fabric-warehouse", "postgres", "mysql"),
        help="SQL dialect (default tsql)",
    )
    d.add_argument("--schema-name", metavar="SCHEMA", help="qualify every table with this schema")
    d.add_argument("--drop", action="store_true", help="drop each table before creating it")
    d.add_argument("-o", "--output", metavar="OUT", help="write the DDL (or the design input) here")
    d.add_argument("--json", metavar="RESULT.json", help="also write the derived tables as JSON")
    d.add_argument(
        "--tmdl",
        metavar="DIR",
        help="also write a TMDL semantic model (DIR/definition/...) for a star or snowflake "
        "design; without -o the DDL is then not printed",
    )
    d.add_argument("--lint", action="store_true", help="print the lint report as JSON, no DDL")
    d.add_argument("--strict", action="store_true", help="warnings fail too (exit 1)")
    d.add_argument(
        "--from-data", action="store_true", help="INPUT is a data file: write a design input"
    )
    d.add_argument("--name", metavar="NAME", help="with --from-data: the design and entity name")


def _write(path: str | None, text: str) -> None:
    if path is None:
        sys.stdout.write(text)
    else:
        Path(path).write_text(text, encoding="utf-8", newline="\n")


def run(a: Any) -> int:
    from shape.design import load_design

    if a.from_data:
        return _from_data(a)
    design = load_design(a.input)
    from shape.design.lint import lint

    findings = lint(design, a.mode)
    failing = [
        f for f in findings if f.severity == "error" or (a.strict and f.severity == "warning")
    ]
    if a.lint:
        _write(
            a.output, json.dumps([f.to_dict() for f in findings], indent=2, sort_keys=True) + "\n"
        )
        return 1 if failing else 0
    for f in findings:
        print(f"{f.severity}: {f.code} {f.path}: {f.message}", file=sys.stderr)
    if failing:
        return 1
    from shape.design.ddl import emit_ddl
    from shape.design.engine import derive

    result = derive(design, a.mode)
    if a.tmdl:
        from shape.design.tmdl import write_tmdl

        write_tmdl(result, a.tmdl, source=design)
        print(f"TMDL written to {Path(a.tmdl) / 'definition'}", file=sys.stderr)
        if not a.output:
            return _write_json_result(a, result)
    sql = emit_ddl(result, a.dialect, schema_name=a.schema_name, drop=a.drop)
    _write_json_result(a, result)
    _write(a.output, sql)
    return 0


def _write_json_result(a: Any, result: Any) -> int:
    if a.json:
        Path(a.json).write_text(
            json.dumps(result.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    return 0


def _from_data(a: Any) -> int:
    from shape.design.from_data import design_from_rows
    from shape.io import iter_rows

    name = a.name or Path(a.input).stem
    design = design_from_rows(iter_rows(a.input), name=name)
    _write(a.output, json.dumps(design.to_dict(), indent=2, sort_keys=True) + "\n")
    return 0
