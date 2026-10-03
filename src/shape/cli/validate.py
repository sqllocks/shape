"""``shape validate FILE``: dispatch on what the file holds (P4-10).

* a Shape generation schema (``schema_version``, ``model`` and ``tables``) goes through the schema
  validator: the JSON Schema, then ``GenSchema.validate`` (tables, keys, relationships, rules, scale
  presets and each strategy's required keys);
* a contract (``name`` and ``fields``) goes through the contract validation;
* anything else is not something Shape reads: exit 2.

Exit 0 valid, 1 invalid, 2 not a Shape document or unreadable.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def load_document(path: Path) -> Any:
    """A schema or contract file: YAML for ``.yaml``/``.yml``, else JSON. A file that is not
    text, or not JSON, raises the usual error with the file named in its message."""
    text = read_text(path)
    if path.suffix.lower() in (".yaml", ".yml"):
        try:
            import yaml  # type: ignore[import-untyped]  # noqa: F401
        except ImportError as exc:
            raise ImportError("reading YAML needs PyYAML: pip install pyyaml") from exc
        from shape.security.yamlsafe import safe_load_yaml

        return safe_load_yaml(text)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:  # the same type, so callers (the bridge) still see it
        raise json.JSONDecodeError(f"{exc.msg} (in {path})", exc.doc, exc.pos) from None


def read_text(path: Path) -> str:
    """The UTF-8 text of ``path``; a binary file is a ``UnicodeDecodeError`` that names it."""
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise UnicodeDecodeError(
            exc.encoding, exc.object, exc.start, exc.end, f"{exc.reason} (in {path})"
        ) from None


_load = load_document  # the name the bridge imports


def kind_of(doc: Any) -> str | None:
    """``generation-schema``, ``contract`` or None."""
    if not isinstance(doc, dict):
        return None
    if {"schema_version", "model", "tables"} <= doc.keys():
        return "generation-schema"
    if "name" in doc and "fields" in doc and "tables" not in doc:
        return "contract"
    return None


def _validate_schema(doc: dict[str, Any]) -> tuple[dict[str, Any], int]:
    from shape.generation.schema import GenSchema, GenSchemaError

    try:
        schema = GenSchema.from_dict(doc)
    except GenSchemaError as exc:
        return {"valid": False, "kind": "generation-schema", "errors": [str(exc)]}, 1
    issues = schema.validate()
    errors = [i for i in issues if i.level == "error"]
    result = {
        "valid": not errors,
        "kind": "generation-schema",
        "name": schema.model.name,
        "mode": schema.model.schema_mode,
        "tables": len(schema.tables),
        "errors": [f"[{i.location}] {i.message}" for i in errors],
        "warnings": [f"[{i.location}] {i.message}" for i in issues if i.level == "warning"],
    }
    return result, 1 if errors else 0


def _validate_contract(doc: dict[str, Any]) -> tuple[dict[str, Any], int]:
    from shape.spec.model import ShapeContract

    try:
        c = ShapeContract.from_dict(doc)
    except (ValueError, KeyError, TypeError) as exc:
        return {"valid": False, "kind": "contract", "errors": [f"{type(exc).__name__}: {exc}"]}, 1
    return {
        "valid": True,
        "kind": "contract",
        "name": c.name,
        "version": c.version,
        "fidelity": c.fidelity,
    }, 0


def cmd_validate(a: argparse.Namespace) -> int:
    path = Path(a.contract)
    doc = load_document(path)
    kind = kind_of(doc)
    if kind is None:
        raise ValueError(
            f"{path} is neither a Shape generation schema (schema_version, model, tables) "
            "nor a contract (name, fields)"
        )
    result, code = (_validate_schema if kind == "generation-schema" else _validate_contract)(doc)
    print(json.dumps(result, sort_keys=True, default=str))
    return code
