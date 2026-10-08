"""A small JSON Schema checker for the subset the bridge schemas use (so the tests need no extra
package): type (a name or a list), enum, const, properties, required, additionalProperties (bool
or schema), items, minimum, maximum, maxLength, pattern, anyOf, oneOf, allOf. ``validate`` returns
the list of problems, each prefixed by the path."""

from __future__ import annotations

import re
from typing import Any


def _type_ok(name: str, value: Any) -> bool:
    if name == "string":
        return isinstance(value, str)
    if name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if name == "number":
        return isinstance(value, int | float) and not isinstance(value, bool)
    if name == "boolean":
        return isinstance(value, bool)
    if name == "object":
        return isinstance(value, dict)
    if name == "array":
        return isinstance(value, list)
    if name == "null":
        return value is None
    raise AssertionError(f"unknown type {name}")


def validate(
    value: Any, schema: dict[str, Any], path: str = "$", wildcard: Any = None
) -> list[str]:
    """``wildcard``, when given, matches any schema (a stored vector's ``<any>``)."""
    if wildcard is not None and value == wildcard:
        return []
    problems: list[str] = []
    if "const" in schema and value != schema["const"]:
        problems.append(f"{path}: expected {schema['const']!r}, got {value!r}")
    if "enum" in schema and value not in schema["enum"]:
        problems.append(f"{path}: {value!r} is not one of {schema['enum']}")
    if "type" in schema:
        names = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_type_ok(n, value) for n in names):
            problems.append(f"{path}: expected {names}, got {type(value).__name__}")
            return problems
    if "anyOf" in schema:
        if not any(not validate(value, s, path, wildcard) for s in schema["anyOf"]):
            problems.append(f"{path}: matches none of anyOf")
    if "oneOf" in schema:
        hits = sum(1 for s in schema["oneOf"] if not validate(value, s, path, wildcard))
        if hits != 1:
            problems.append(f"{path}: matches {hits} of oneOf (needs exactly 1)")
    for sub in schema.get("allOf", []):
        problems.extend(validate(value, sub, path, wildcard))
    if isinstance(value, str):
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            problems.append(f"{path}: longer than {schema['maxLength']}")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            problems.append(f"{path}: does not match {schema['pattern']}")
    if isinstance(value, int | float) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            problems.append(f"{path}: below {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            problems.append(f"{path}: above {schema['maximum']}")
    if isinstance(value, dict):
        props = schema.get("properties", {})
        for name in schema.get("required", []):
            if name not in value:
                problems.append(f"{path}: missing {name!r}")
        extra = schema.get("additionalProperties", True)
        for name, item in value.items():
            if name in props:
                problems.extend(validate(item, props[name], f"{path}.{name}", wildcard))
            elif extra is False:
                problems.append(f"{path}: unexpected {name!r}")
            elif isinstance(extra, dict):
                problems.extend(validate(item, extra, f"{path}.{name}", wildcard))
    if isinstance(value, list) and "items" in schema:
        for i, item in enumerate(value):
            problems.extend(validate(item, schema["items"], f"{path}[{i}]", wildcard))
    return problems
