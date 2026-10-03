"""OpenAPI 3.0 and 3.1 to the importer model (W5-06): one table per object schema of
``components.schemas``, with ``$ref`` between schemas becoming relationships. The schema rules
are those of :mod:`shape.importers.jsonschema`; ``paths`` and the other component kinds are not
imported and the report says so."""

from __future__ import annotations

from typing import Any

from shape.importers.core import ImpModel, ImportFormatError, Report
from shape.importers.documents import Document
from shape.importers.jsonschema import SchemaWalker, _escape, root_name

_OTHER_COMPONENTS = (
    "parameters",
    "responses",
    "requestBodies",
    "headers",
    "securitySchemes",
    "links",
    "callbacks",
    "examples",
    "pathItems",
)


def import_openapi(doc: Document, report: Report) -> ImpModel:
    data: Any = doc.data
    if not isinstance(data, dict) or not isinstance(data.get("openapi"), str):
        raise ImportFormatError("not an OpenAPI document: no openapi version", file=doc.path)
    version = data["openapi"]
    if not (version.startswith("3.0") or version.startswith("3.1")):
        raise ImportFormatError(
            f"OpenAPI {version} is not supported (3.0 and 3.1 are)",
            file=doc.path,
            line=doc.line("/openapi"),
            element="#/openapi",
        )
    report.mapped("#/openapi", "version", f"OpenAPI {version}")
    components = data.get("components")
    schemas = components.get("schemas") if isinstance(components, dict) else None
    if not isinstance(schemas, dict) or not schemas:
        raise ImportFormatError(
            "components.schemas is missing or empty: there is nothing to import",
            file=doc.path,
            element="#/components/schemas",
        )
    title = (data.get("info") or {}).get("title") if isinstance(data.get("info"), dict) else None
    walker = SchemaWalker(doc, report, str(title or root_name(doc.path)))
    for name, sub in schemas.items():
        ptr = f"/components/schemas/{_escape(str(name))}"
        eff, _ = walker.effective(sub, ptr, "schema")
        if eff is None:
            continue
        if walker.kind(eff) == "object" and eff.get("properties"):
            walker.named_table(ptr, str(name), sub)
        else:
            report.skipped(
                walker.at(ptr),
                "schema",
                f"a {walker.kind(eff)} schema without properties is not a table "
                "(it is used where other schemas refer to it)",
            )
    if "paths" in data:
        report.skipped(
            "#/paths", "paths", "paths are not imported: only components.schemas is read"
        )
    if isinstance(components, dict):
        for key in _OTHER_COMPONENTS:
            if key in components:
                report.skipped(
                    f"#/components/{key}", "components", f"components.{key} is not imported"
                )
    if not walker.model.tables:
        raise ImportFormatError(
            "components.schemas has no object schema with properties",
            file=doc.path,
            element="#/components/schemas",
        )
    return walker.finish()
