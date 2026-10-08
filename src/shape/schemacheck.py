"""A small JSON-Schema checker (a subset of Draft 2020-12), so schemas shipped with Shape can be
enforced without adding a dependency.

Supported: ``type`` (also a list), ``enum``, ``const``, ``required``, ``properties``,
``patternProperties``, ``additionalProperties`` (false or a schema), ``items``, ``minItems``,
``minimum``, ``maximum``, ``pattern`` (not anchored: a search, as in JSON Schema), ``anyOf``,
``allOf``, ``not``, ``if``/``then`` and local ``$ref`` (``#/$defs/...``). Anything else in a schema
is ignored, so schemas that use other keywords are only checked for the ones listed here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
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


@dataclass(frozen=True, slots=True)
class Problem:
    """One violation: ``path`` is the keys and indexes from the document root, ``message`` what
    is wrong at that place; ``key`` is the offending key of the object at ``path`` for an
    unexpected key."""

    path: tuple[str | int, ...]
    message: str
    key: str | None = None


def format_path(path: tuple[str | int, ...]) -> str:
    """``$.a.b[2]`` for ``("a", "b", 2)``."""
    return "$" + "".join(f"[{p}]" if isinstance(p, int) else f".{p}" for p in path)


def validate(value: Any, schema: dict[str, Any]) -> list[str]:
    """The list of violations of ``schema`` by ``value`` (empty when valid)."""
    return [f"{format_path(p.path)}: {p.message}" for p in problems(value, schema)]


def problems(
    value: Any,
    schema: dict[str, Any],
    path: tuple[str | int, ...] = (),
    root: dict[str, Any] | None = None,
) -> list[Problem]:
    """The violations of ``schema`` by ``value`` with their locations (empty when valid)."""
    root = root or schema
    if "$ref" in schema:
        node: Any = root
        for part in schema["$ref"].lstrip("#/").split("/"):
            node = node[part]
        return problems(value, node, path, root)
    if "anyOf" in schema:
        errs = [problems(value, s, path, root) for s in schema["anyOf"]]
        if any(not e for e in errs):
            return []
        first = [f"{format_path(p.path)}: {p.message}" for p in errs[0][:1]]
        return [Problem(path, f"matches none of anyOf ({first}...)")]
    out: list[Problem] = []

    def add(message: str, key: str | None = None) -> None:
        out.append(Problem(path, message, key))

    if "const" in schema and value != schema["const"]:
        add(f"expected {schema['const']!r}, got {value!r}")
    if "enum" in schema and value not in schema["enum"]:
        add(f"{value!r} not in {schema['enum']}")
    t = schema.get("type")
    if t is not None:
        types = t if isinstance(t, list) else [t]
        if not any(_is_type(value, x) for x in types):
            return [*out, Problem(path, f"expected {types}, got {type(value).__name__}")]
    if "minimum" in schema and _is_type(value, "number") and value < schema["minimum"]:
        add(f"{value} < minimum {schema['minimum']}")
    if "maximum" in schema and _is_type(value, "number") and value > schema["maximum"]:
        add(f"{value} > maximum {schema['maximum']}")
    if "pattern" in schema and isinstance(value, str) and not re.search(schema["pattern"], value):
        add(f"{value!r} does not match pattern {schema['pattern']!r}")
    if "not" in schema and not problems(value, schema["not"], path, root):
        add("matches a schema it must not match")
    for sub in schema.get("allOf", []):
        out += problems(value, sub, path, root)
    if "if" in schema and "then" in schema and not problems(value, schema["if"], path, root):
        out += problems(value, schema["then"], path, root)
    if isinstance(value, dict):
        for k in schema.get("required", []):
            if k not in value:
                add(f"missing required key {k!r}")
        props = schema.get("properties", {})
        patterns = schema.get("patternProperties", {})
        extra = schema.get("additionalProperties")
        for k, v in value.items():
            matched = [sub for pat, sub in patterns.items() if re.search(pat, k)]
            if k in props:
                out += problems(v, props[k], (*path, k), root)
            for sub in matched:
                out += problems(v, sub, (*path, k), root)
            if k in props or matched:
                continue
            if extra is False:
                add(f"unexpected key {k!r}", k)
            elif isinstance(extra, dict):
                out += problems(v, extra, (*path, k), root)
    if isinstance(value, (list, tuple)):
        if "minItems" in schema and len(value) < schema["minItems"]:
            add(f"{len(value)} items, fewer than minItems {schema['minItems']}")
        if "items" in schema:
            for i, v in enumerate(value):
                out += problems(v, schema["items"], (*path, i), root)
    return out
