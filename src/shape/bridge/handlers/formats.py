"""``format_schema``: the JSON Schemas Shape's readers validate their input with (bridge 1.1).

:data:`FORMATS` is the one table that says which schema file shipped in ``shape/schemas`` is
published under which name. A test fails when a shipped file is not in the table, so a new format
cannot ship without a name here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import resources
from typing import Any

from shape.bridge.context import Context
from shape.bridge.handlers.common import ANY, INT, STR, STRS, mapping, nullable, obj
from shape.bridge.protocol import BridgeError
from shape.bridge.spec import Arg, Command


@dataclass(frozen=True)
class Format:
    """A published format. ``format`` is the document's own ``format`` value, ``None`` for a
    document that identifies itself by ``schema_version`` only; ``version`` is its version."""

    file: str
    format: str | None
    version: int
    reader: str


FORMATS: dict[str, Format] = {
    "design-input": Format("design-input-v1.json", "shape-design", 1, "shape.design.load_design"),
    "generation-schema": Format(
        "generation-schema-v1.json", None, 1, "shape.generation.schema.GenSchema"
    ),
    "decisions": Format(
        "decisions-v1.schema.json", "shape-decisions", 1, "shape.proposals.DecisionFile"
    ),
    "project": Format(
        "shape-project-v1.schema.json", "shape-project", 1, "shape.project.load_project"
    ),
    "model": Format("shape-v2.schema.json", None, 2, "shape.spec.model"),
    "model-v1": Format("shape-v1.schema.json", None, 1, "shape.spec.migrate"),
    "model-v1-ga": Format("shape-v1-ga.schema.json", None, 1, "shape.spec.migrate"),
    "profile-engine": Format("profile-engine-v1.schema.json", None, 1, "shape.profile.engine"),
}


def shipped_files() -> list[str]:
    """The schema files shipped in ``shape/schemas``."""
    folder = resources.files("shape").joinpath("schemas")
    return sorted(p.name for p in folder.iterdir() if p.name.endswith(".json"))


def cmd_format_schema(args: dict[str, Any], ctx: Context) -> dict[str, Any]:
    name = args.get("name")
    if name is None:
        return {"names": sorted(FORMATS)}
    known = FORMATS.get(str(name))
    if known is None:
        raise BridgeError(
            "input.unknown_format",
            f"no published format named {name!r} (formats: {', '.join(sorted(FORMATS))})",
            "call format_schema without a name for the list",
        )
    text = resources.files("shape").joinpath("schemas", known.file).read_text("utf-8")
    return {
        "name": str(name),
        "format": known.format,
        "version": known.version,
        "schema": json.loads(text),
    }


COMMANDS = [
    Command(
        "format_schema",
        "List the formats Shape reads, or give the JSON Schema of one.",
        {
            "name": Arg(
                "string",
                "a format name; without it the result lists the names available "
                "(`design-input`, `generation-schema`, `decisions`, `project`, ...)",
            )
        },
        obj(
            {},
            {
                "names": STRS,
                "name": STR,
                "format": nullable(STR),
                "version": INT,
                "schema": mapping(ANY),
            },
        ),
        cmd_format_schema,
        since="1.1",
    )
]
