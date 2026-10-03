"""``shape import-schema``: a JSON Schema, OpenAPI, Avro, Protobuf, Pydantic or TMDL source to a
generation spec, with a mapping report. See ``docs/IMPORTERS.md``."""

from __future__ import annotations

import sys
from typing import Any

FORMATS = ("jsonschema", "openapi", "avro", "protobuf", "pydantic", "tmdl")


def add_parsers(sub: Any) -> None:
    p = sub.add_parser(
        "import-schema",
        help="import a JSON Schema, OpenAPI, Avro, Protobuf, Pydantic or TMDL schema as a spec",
        description="Reads the structure of a schema (no data) and writes a generation spec that "
        "validates against the published schema, choosing each column's strategy from its type "
        "and constraints. The report lists every source element and what it became, and every "
        "element that was not imported with the reason. Exit codes: 0 ok, 1 an element was not "
        "imported (with --strict; no spec is written), 2 bad or ambiguous input.",
    )
    p.add_argument(
        "file",
        metavar="FILE",
        help="the schema file, a TMDL folder, or module.path:ModelName for --from pydantic",
    )
    p.add_argument(
        "--from",
        dest="source_format",
        choices=FORMATS,
        help="the format (inferred from the extension and content when omitted)",
    )
    p.add_argument("-o", "--output", metavar="OUT.gen.json", required=True, help="the spec")
    p.add_argument("--report", metavar="REPORT.json", help="write the mapping report here")
    p.add_argument(
        "--strict", action="store_true", help="exit 1 and write no spec if anything is not imported"
    )
    p.add_argument(
        "--allow-import",
        action="store_true",
        help="for --from pydantic: import (run) the named module, which is the user's own code",
    )


def run(a: Any) -> int:
    from shape.importers import import_schema
    from shape.importers.core import write_outputs

    result = import_schema(a.file, a.source_format, allow_import=a.allow_import)
    report = result.report
    if a.strict and report.not_imported:
        write_outputs(None, report, None, a.report)
        first = report.not_imported[0]
        print(
            f"shape: {len(report.not_imported)} element(s) not imported (--strict), no spec "
            f"written; first: {first['element']}: {first['reason']}",
            file=sys.stderr,
        )
        return 1
    write_outputs(result.spec, report, a.output, a.report)
    spec = result.spec.to_dict()
    columns = sum(len(t["columns"]) for t in spec["tables"].values())
    print(f"Shape schema import ({report.source_format})")
    print(f"  Source: {a.file}")
    print(f"  Output: {a.output}")
    print(f"  Tables: {len(spec['tables'])}, columns: {columns}")
    print(f"  Relationships: {len(spec['relationships'])}")
    print(f"  Imported elements: {len(report.imported)}, not imported: {len(report.not_imported)}")
    for item in report.not_imported:
        print(f"  not imported: {item['element']}: {item['reason']}", file=sys.stderr)
    return 0
