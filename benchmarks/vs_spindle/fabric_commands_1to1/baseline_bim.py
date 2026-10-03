"""The baseline's semantic-model exporter on a schema document, in the baseline venv (P6-07c).

    "$SPINDLE_PY" baseline_bim.py DOC.json SOURCE_TYPE SOURCE_NAME INCLUDE_MEASURES SCHEMA_NAME

Prints the TOM model as JSON: the library call behind ``export-model``, for schemas the command
(which only takes a domain's name) cannot be given.
"""

from __future__ import annotations

import json
import sys


def main(argv: list[str]) -> int:
    from sqllocks_spindle.fabric.semantic_model_writer import SemanticModelExporter
    from sqllocks_spindle.schema.parser import SchemaParser

    doc_path, source_type, source_name, measures, schema_name = argv
    with open(doc_path, encoding="utf-8") as fh:
        schema = SchemaParser().parse_dict(json.load(fh))
    tom = SemanticModelExporter().to_dict(
        schema,
        source_type=source_type,
        source_name=source_name,
        include_measures=measures == "1",
        schema_name=schema_name,
    )
    print(json.dumps(tom))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
