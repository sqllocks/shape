"""A small JSON-Schema checker (a subset of Draft 2020-12), so schemas shipped with Shape can be
enforced without adding a dependency.

Supported: ``type`` (also a list), ``enum``, ``const``, ``required``, ``properties``,
``additionalProperties`` (false or a schema), ``items``, ``minimum``, ``maximum``, ``pattern``
(not anchored: a search, as in JSON Schema), ``not``, ``anyOf`` and local ``$ref``
(``#/$defs/...``). Anything else in a schema is ignored, so schemas that use other
keywords are only checked for the ones listed here.
"""

from __future__ import annotations

import re
from typing import Any

_TYPES: dict[str, Any] = {
    "object": dict,
    "array": (list, tuple),
    "string": str,
    "boolean": bool,
    "null": type(None),
}


def _is_type(v: Any, t: str) -> bool:
    if t == "integer":
        return isinstance(v, int) and not isinstance(v, bool)
    if t == "number":
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    return isinstance(v, _TYPES[t])


def validate(
    value: Any, schema: dict[str, Any], path: str = "$", root: dict[str, Any] | None = None
) -> list[str]:
    """The list of violations of ``schema`` by ``value`` (empty when valid)."""
    root = root or schema
    if "$ref" in schema:
        node: Any = root
        for part in schema["$ref"].lstrip("#/").split("/"):
            node = node[part]
        return validate(value, node, path, root)
    if "anyOf" in schema:
        errs = [validate(value, s, path, root) for s in schema["anyOf"]]
        return (
            []
            if any(not e for e in errs)
            else [f"{path}: matches none of anyOf ({errs[0][:1]}...)"]
        )
    out: list[str] = []
    if "const" in schema and value != schema["const"]:
        out.append(f"{path}: expected {schema['const']!r}, got {value!r}")
    if "enum" in schema and value not in schema["enum"]:
        out.append(f"{path}: {value!r} not in {schema['enum']}")
    t = schema.get("type")
    if t is not None:
        types = t if isinstance(t, list) else [t]
        if not any(_is_type(value, x) for x in types):
            return [*out, f"{path}: expected {types}, got {type(value).__name__}"]
    if "minimum" in schema and _is_type(value, "number") and value < schema["minimum"]:
        out.append(f"{path}: {value} < minimum {schema['minimum']}")
    if "maximum" in schema and _is_type(value, "number") and value > schema["maximum"]:
        out.append(f"{path}: {value} > maximum {schema['maximum']}")
    if "pattern" in schema and isinstance(value, str) and not re.search(schema["pattern"], value):
        out.append(f"{path}: {value!r} does not match pattern {schema['pattern']!r}")
    if "not" in schema and not validate(value, schema["not"], path, root):
        out.append(f"{path}: {value!r} matches a schema it must not match")
    if isinstance(value, dict):
        for k in schema.get("required", []):
            if k not in value:
                out.append(f"{path}: missing required key {k!r}")
        props = schema.get("properties", {})
        extra = schema.get("additionalProperties")
        for k, v in value.items():
            if k in props:
                out += validate(v, props[k], f"{path}.{k}", root)
            elif extra is False:
                out.append(f"{path}: unexpected key {k!r}")
            elif isinstance(extra, dict):
                out += validate(v, extra, f"{path}.{k}", root)
    if isinstance(value, list) and "items" in schema:
        for i, v in enumerate(value):
            out += validate(v, schema["items"], f"{path}[{i}]", root)
    return out
