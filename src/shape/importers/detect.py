"""Which format a file is: by extension, then by content (W5-06)."""

from __future__ import annotations

from pathlib import Path

from shape.importers.core import ImportFormatError
from shape.importers.documents import load_document

_BY_SUFFIX = {".avsc": "avro", ".proto": "protobuf", ".py": "pydantic", ".tmdl": "tmdl"}


def _is_avro(node: object) -> bool:
    if isinstance(node, list):
        return (
            bool(node)
            and all(isinstance(n, dict) and "type" in n for n in node)
            and any(_is_avro(n) for n in node)
        )
    return (
        isinstance(node, dict)
        and node.get("type") in ("record", "enum", "fixed", "error")
        and ("fields" in node or "symbols" in node or "size" in node)
    )


def detect(source: str) -> str:
    path = Path(source)
    if ":" in source and not path.exists() and ".py:" in source:
        return "pydantic"
    if path.is_dir():
        if (path / "definition").is_dir() or any(path.glob("*.tmdl")):
            return "tmdl"
        raise ImportFormatError(
            "a folder is a TMDL project only when it has a definition/ folder; "
            "say --from if it is another format",
            file=source,
        )
    suffix = path.suffix.lower()
    if suffix in _BY_SUFFIX:
        return _BY_SUFFIX[suffix]
    if not path.exists():
        raise ImportFormatError("file not found", file=source)
    if suffix not in (".json", ".yaml", ".yml"):
        raise ImportFormatError(
            f"cannot tell the format of a {suffix or 'extensionless'} file: say --from "
            "jsonschema, openapi, avro, protobuf, pydantic or tmdl",
            file=source,
        )
    data = load_document(source).data
    found: list[str] = []
    if isinstance(data, dict):
        if "openapi" in data:
            found.append("openapi")
        elif "swagger" in data:
            raise ImportFormatError(
                "Swagger 2.0 is not supported (OpenAPI 3.0 and 3.1 are)", file=source
            )
        if _is_avro(data):
            found.append("avro")
        if "openapi" not in data and (
            "$schema" in data
            or "properties" in data
            or "$defs" in data
            or "definitions" in data
            or data.get("type") in ("object", "array")
        ):
            found.append("jsonschema")
    elif _is_avro(data):
        found.append("avro")
    if len(found) == 1:
        return found[0]
    raise ImportFormatError(
        "cannot tell the format from the content"
        + (f" (could be {' or '.join(found)})" if found else "")
        + ": say --from jsonschema, openapi or avro",
        file=source,
    )
