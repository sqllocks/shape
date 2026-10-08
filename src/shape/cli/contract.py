"""``shape contract emit``: a contract as DDL, a JSON Schema, a pandera schema or a Great
Expectations suite. See ``docs/CONTRACT_EMIT.md``."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

TARGETS = ("ddl", "jsonschema", "pandera", "gx")
DIALECTS = ("tsql", "tsql-fabric-warehouse", "postgres", "mysql")


def add_parsers(sub: Any) -> None:
    c = sub.add_parser(
        "contract",
        help="work with a contract: emit it as DDL, JSON Schema, pandera or Great Expectations",
        description="`shape contract emit CONTRACT.json --to TARGET` writes a v1 contract (the "
        "one `shape check` reads) where other tools validate data. The same contract always gives "
        "the same bytes. Exit 0 written; 1 with --strict when a rule cannot be expressed (nothing "
        "is written); 2 for a malformed contract, an unknown target or --dialect with a target "
        "other than ddl.",
    )
    csub = c.add_subparsers(dest="contract_cmd", required=True)
    e = csub.add_parser("emit", help="emit a contract as DDL, JSON Schema, pandera or GX")
    e.add_argument("contract", metavar="CONTRACT.json")
    e.add_argument(
        "--to", required=True, metavar="TARGET", help=f"the target: {', '.join(TARGETS)}"
    )
    e.add_argument("-o", "--output", metavar="OUT", help="write the text here (default: stdout)")
    e.add_argument(
        "--table",
        metavar="NAME",
        help="emit this table of a `tables` contract (jsonschema and gx need one); for a "
        "single-table contract, the table's name (default: the file name without .json and "
        ".contract)",
    )
    e.add_argument(
        "--dialect",
        metavar="DIALECT",
        help=f"ddl only: the SQL dialect, one of {', '.join(DIALECTS)} (default tsql)",
    )
    e.add_argument(
        "--strict",
        action="store_true",
        help="exit 1, listing them and writing nothing, when any rule cannot be expressed",
    )
    e.add_argument(
        "--json",
        action="store_true",
        help="print the result (format shape-contract-emit, version 1) as JSON on stdout, under "
        "payload of the shape-result document",
    )


def _default_table(path: str) -> str:
    stem = Path(path).name
    for suffix in (".json", ".contract"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
    return stem or "table"


def run(a: Any) -> int:
    from shape.contracts.emit import emit
    from shape.contracts.v1 import _load_contract

    options: dict[str, Any] = {}
    if a.dialect is not None:
        options["dialect"] = a.dialect
    contract = _load_contract(a.contract)
    if a.table is not None:
        options["table"] = a.table
    elif "tables" not in contract:
        options["table"] = _default_table(a.contract)
    result = emit(contract, a.to, **options)
    if a.json:
        sys.stdout.write(json.dumps(result.to_dict(), indent=2, sort_keys=True) + "\n")
    if a.strict and result.not_expressed:
        for item in result.not_expressed:
            where = item["table"] + (f".{item['column']}" if item["column"] else "")
            print(
                f"not expressible in {a.to}: {where}: {item['rule']}: {item['reason']}",
                file=sys.stderr,
            )
        return 1
    if a.output:
        Path(a.output).write_text(result.text, encoding="utf-8", newline="\n")
    elif not a.json:
        sys.stdout.write(result.text)
    return 0
