"""Schema importers (W5-06): JSON Schema, OpenAPI, Avro, Protobuf, Pydantic and TMDL to a
generation spec. See ``docs/IMPORTERS.md``.

    from shape.importers import import_schema
    result = import_schema("order.schema.json")      # the format is inferred
    result.spec.save("order.gen.json")
    print(result.report.dumps())

Stable interface: :func:`import_schema`, :func:`detect_format`, :class:`ImportResult`,
:class:`Report` and the two errors. Each format's module loads on first use.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from shape.generation.spec_edit import SpecDocument
from shape.importers.core import (
    ImportFormatError,
    Report,
    StrictImportError,
    build_spec,
)

FORMATS = ("jsonschema", "openapi", "avro", "protobuf", "pydantic", "tmdl")

__all__ = [
    "FORMATS",
    "ImportFormatError",
    "ImportResult",
    "Report",
    "StrictImportError",
    "detect_format",
    "import_schema",
]


@dataclass
class ImportResult:
    """The spec and the mapping report of one import."""

    spec: SpecDocument
    report: Report


def detect_format(source: str | Path) -> str:
    """The format of ``source`` from its extension and content; an :class:`ImportFormatError`
    asks for ``--from`` when it cannot tell (or the file is of a kind that is not supported)."""
    from shape.importers.detect import detect

    return detect(str(source))


def import_schema(
    source: str | Path,
    fmt: str | None = None,
    *,
    allow_import: bool = False,
) -> ImportResult:
    """Import ``source`` (a file, a TMDL folder, or ``module.path:Model`` for ``pydantic``).

    ``allow_import`` is required for ``pydantic``, which imports (runs) the user's module.
    Raises :class:`ImportFormatError` for a malformed or unsupported input."""
    name = str(source)
    chosen = fmt or detect_format(name)
    if chosen not in FORMATS:
        raise ImportFormatError(f"unknown format {chosen!r}; choose one of {', '.join(FORMATS)}")
    try:
        return _import(name, chosen, allow_import)
    except ImportFormatError as exc:
        raise exc.located(name) from exc


def _import(name: str, chosen: str, allow_import: bool) -> ImportResult:
    report = Report(name, chosen)
    if chosen == "jsonschema":
        from shape.importers.documents import load_document
        from shape.importers.jsonschema import import_jsonschema

        model = import_jsonschema(load_document(name), report)
    elif chosen == "openapi":
        from shape.importers.documents import load_document
        from shape.importers.openapi import import_openapi

        model = import_openapi(load_document(name), report)
    elif chosen == "avro":
        from shape.importers.avro import import_avro

        model = import_avro(name, report)
    elif chosen == "protobuf":
        from shape.importers.protobuf import import_protobuf

        model = import_protobuf(name, report)
    elif chosen == "pydantic":
        from shape.importers.pydantic_models import import_pydantic

        model = import_pydantic(name, report, allow_import=allow_import)
    else:
        from shape.importers.tmdl import import_tmdl

        model = import_tmdl(name, report)
    return ImportResult(build_spec(model, report), report)
