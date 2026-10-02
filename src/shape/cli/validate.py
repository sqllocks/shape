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


def _load(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in (".yaml", ".yml"):
        try:
            import yaml  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ImportError("reading YAML needs PyYAML: pip install pyyaml") from exc
        return yaml.safe_load(text)
    return json.loads(text)


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
    doc = _load(path)
    kind = kind_of(doc)
    if kind is None:
        raise ValueError(
            f"{path} is neither a Shape generation schema (schema_version, model, tables) "
            "nor a contract (name, fields)"
        )
    result, code = (_validate_schema if kind == "generation-schema" else _validate_contract)(doc)
    print(json.dumps(result, sort_keys=True, default=str))
    return code
