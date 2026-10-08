"""The published JSON Schema of the generation spec: ``schemas/generation-spec-v1.schema.json``.

It is *built* from the code, so it cannot drift: the strategy names come from the strategy
registry (``shape.builtins.catalog``), each strategy's keys and nested ``params`` from
:mod:`shape.generation.spec_keys`, its required keys from
:data:`shape.generation.schema.STRATEGY_REQUIRED_KEYS` and the structure around the generators
from the loader's schema (``generation-schema-v1.json``). ``tests/generation/
test_w1_06_spec_schema.py`` checks that the shipped file is exactly what :func:`build_schema`
returns. After changing a strategy's keys, regenerate it::

    python -m shape.generation.spec_schema

The loader's schema stays lenient about generator keys (an unknown key is a validator warning,
so an old file with one still loads); this one is strict, because editors and form builders
want a typo reported. A strategy that is not built in (a plugin's) and a distribution family
that is not built in are open: any keys are accepted. Every object also accepts ``x-*`` keys
and ``$comment``, the extension points of the format (``docs/GENERATION_SPEC.md``).
"""

from __future__ import annotations

import copy
import json
import sys
from functools import cache
from importlib import resources
from pathlib import Path
from typing import Any

from shape.generation import spec_keys
from shape.generation.schema import STRATEGY_REQUIRED_KEYS, json_schema
from shape.plugins.host import default_host
from shape.plugins.registry import SOURCE

__all__ = ["build_schema", "published_schema", "render", "strategy_names"]

SPEC_FORMAT = "generation-spec"
SPEC_VERSION = 1
SCHEMA_FILE = "schemas/generation-spec-v1.schema.json"
SCHEMA_ID = "https://shape-as-code.org/schema/1.0/generation-spec-v1.schema.json"

# Extension points: an ``x-`` key or ``$comment`` is accepted on every object and is carried
# through load, edit and save untouched.
EXTENSIONS: dict[str, dict[str, Any]] = {"^(x-|\\$comment$)": {}}

# Value types that are the same wherever a strategy takes the key. Everything not listed is
# unconstrained here and checked by the strategy itself.
_KEY_SCHEMAS: dict[tuple[str, str], dict[str, Any]] = {
    ("faker", "provider"): {"type": "string"},
    ("native", "provider"): {"type": "string"},
    ("formula", "expression"): {"type": "string"},
    ("pattern", "format"): {"type": "string"},
    ("conditional", "condition"): {"type": "string"},
    ("foreign_key", "ref"): {"type": "string"},
    ("composite_foreign_key", "ref_table"): {"type": "string"},
    ("composite_foreign_key", "ref_columns"): {"type": "array", "items": {"type": "string"}},
    ("reference_data", "dataset"): {"type": "string"},
    ("bootstrap", "dataset"): {"type": "string"},
    ("record_sample", "dataset"): {"type": "string"},
    ("record_field", "dataset"): {"type": "string"},
    ("lookup", "source_table"): {"type": "string"},
    ("lookup", "source_column"): {"type": "string"},
    ("correlated", "source_column"): {"type": "string"},
    ("computed", "child_table"): {"type": "string"},
    ("computed", "child_column"): {"type": "string"},
    ("self_referencing", "pk_column"): {"type": "string"},
    ("first_per_parent", "parent_column"): {"type": "string"},
}


# Keys that profile-derived specs (``shape.generation.learn``) have always written and the
# strategy ignores (``GenSchema.validate`` warns about them). The schema accepts them so those
# files stay valid in 1.x; they are marked ``deprecated`` and are not part of the strategy's
# contract.
LEGACY_IGNORED_KEYS: dict[str, frozenset[str]] = {
    "temporal": frozenset({"type"}),
    "faker": frozenset({"max_length"}),
}


def strategy_names() -> list[str]:
    """The built-in strategy names from the plugin registry (a plugin's own are not listed)."""
    records = default_host().records("shape.strategies")
    return sorted(r.name for r in records if r.source == SOURCE)


def _object(properties: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {"type": "object", "properties": properties, **extra}


def _output_type() -> dict[str, Any]:
    from shape.generation.engine import _OUTPUT_TYPES, DECLARED_TYPES

    return {"enum": [*_OUTPUT_TYPES, *DECLARED_TYPES]}


def _properties(
    strategy: str, keys: frozenset[str], nested: frozenset[str] | None
) -> dict[str, Any]:
    props: dict[str, Any] = {}
    for key in sorted(keys | LEGACY_IGNORED_KEYS.get(strategy, frozenset())):
        if key == "strategy":
            props[key] = {"const": strategy}
        elif key == "output_type":
            props[key] = _output_type()
        elif key in LEGACY_IGNORED_KEYS.get(strategy, frozenset()) and key not in keys:
            props[key] = {
                "deprecated": True,
                "description": "Written by older tools and ignored by the strategy.",
            }
        else:
            props[key] = copy.deepcopy(_KEY_SCHEMAS.get((strategy, key), {}))
    if nested is not None:
        props["params"] = _object(
            {k: {} for k in sorted(nested)},
            additionalProperties=False,
            patternProperties=EXTENSIONS,
        )
    return props


def _is_strategy(name: str) -> dict[str, Any]:
    return {"properties": {"strategy": {"const": name}}, "required": ["strategy"]}


def _closed(
    strategy: str, keys: frozenset[str], nested: frozenset[str] | None, required: list[str]
) -> dict[str, Any]:
    then = _object(_properties(strategy, keys, nested), additionalProperties=False)
    then["patternProperties"] = EXTENSIONS
    if required:
        then["required"] = required
    return then


def _distribution_branch() -> dict[str, Any]:
    """``distribution`` reads the keys of the family it names (``uniform`` when it names none)."""
    base = spec_keys.COMMON | spec_keys.STRATEGY_KEYS["distribution"]
    top = _object(
        {
            "distribution": {"type": "string"},
            "params": {"type": "object"},
            "min": {},
            "max": {},
            "output_type": _output_type(),
        }
    )
    families: list[dict[str, Any]] = []
    for family in sorted(spec_keys.FAMILY_KEYS):
        allowed = base | spec_keys.FAMILY_KEYS[family]
        inner = allowed - {"params", "distribution", "strategy", "output_type"}
        named = {"properties": {"distribution": {"const": family}}, "required": ["distribution"]}
        cond: dict[str, Any] = named
        if family == "uniform":
            cond = {"anyOf": [named, {"not": {"required": ["distribution"]}}]}
        families.append({"if": cond, "then": _closed("distribution", allowed, inner, [])})
    # ``distribution`` is not required: a generator that names no family is a uniform.
    top["allOf"] = families
    return top


def _generator() -> dict[str, Any]:
    branches: list[dict[str, Any]] = []
    for name in strategy_names():
        if name == "distribution":
            then = _distribution_branch()
        else:
            required = sorted(STRATEGY_REQUIRED_KEYS.get(name, ()))
            then = _closed(
                name,
                spec_keys.COMMON | spec_keys.STRATEGY_KEYS[name],
                spec_keys.PARAMS_KEYS.get(name),
                required,
            )
        branches.append({"if": _is_strategy(name), "then": then})
    return {
        "type": "object",
        "description": (
            "A strategy name plus the keys that strategy reads. A strategy that is not built "
            "in (a plugin's) accepts any keys."
        ),
        "properties": {"strategy": {"type": "string", "examples": strategy_names()}},
        "patternProperties": EXTENSIONS,
        "allOf": branches,
    }


def _open_extensions(node: Any) -> None:
    """Allow ``x-`` keys and ``$comment`` on every closed object of the loader's schema."""
    if isinstance(node, dict):
        if node.get("additionalProperties") is False:
            node.setdefault("patternProperties", EXTENSIONS)
        for value in node.values():
            _open_extensions(value)
    elif isinstance(node, list):
        for value in node:
            _open_extensions(value)


def build_schema() -> dict[str, Any]:
    """The published schema: the loader's structure with a strict ``generator`` per strategy."""
    schema = copy.deepcopy(json_schema())
    schema["$id"] = SCHEMA_ID
    schema["title"] = "Shape generation spec, version 1"
    schema["description"] = (
        "A generation spec: a model (seed, locale, date range, mode), tables whose columns each "
        "carry a generator (a strategy name plus the keys that strategy reads), relationships, "
        "business rules, scale presets and derived row counts, and correlated column pairs. "
        "Stable within 1.x (docs/GENERATION_SPEC.md): keys and strategies are only ever added."
    )
    schema["x-shape-format"] = SPEC_FORMAT
    schema["x-shape-version"] = SPEC_VERSION
    schema["$defs"]["column"]["properties"]["generator"] = {"$ref": "#/$defs/generator"}
    schema["$defs"]["generator"] = _generator()
    _open_extensions(schema)
    schema["$defs"]["generator"]["patternProperties"] = EXTENSIONS
    schema["patternProperties"] = EXTENSIONS
    return schema


@cache
def published_schema() -> dict[str, Any]:
    """The schema as shipped in ``shape/schemas``."""
    text = resources.files("shape").joinpath(SCHEMA_FILE).read_text("utf-8")
    schema: dict[str, Any] = json.loads(text)
    return schema


def render() -> str:
    """The text of the shipped file."""
    return json.dumps(build_schema(), indent=2) + "\n"


def main(argv: list[str] | None = None) -> int:
    """Write the shipped file (``--check`` only compares it)."""
    args = sys.argv[1:] if argv is None else argv
    target = Path(str(resources.files("shape").joinpath(SCHEMA_FILE)))
    if "--check" in args:
        same = target.read_text("utf-8") == render()
        print("generation spec schema is current" if same else "generation spec schema is stale")
        return 0 if same else 1
    target.write_text(render(), encoding="utf-8", newline="\n")
    print(f"wrote {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
