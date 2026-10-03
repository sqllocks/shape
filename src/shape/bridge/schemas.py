"""The published JSON Schemas of the bridge (P6-11).

Everything is generated from the command table, so the schemas cannot drift from the code. They
are committed under ``docs/bridge/schema/`` and a test fails when the committed files differ from
what this module generates: changing a schema is a visible, deliberate edit
(``shape bridge schema --out docs/bridge/schema``).

Files: ``index.json``; ``request.schema.json`` and ``response.schema.json`` (the envelope);
``error.schema.json``; ``job.schema.json``; and per command ``commands/NAME.request.schema.json``
(the whole request) and ``commands/NAME.result.schema.json`` (the ``result`` of a success).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from shape.bridge.handlers.scale import JOB
from shape.bridge.protocol import API_VERSION, ERROR_CODES, GROUPS
from shape.bridge.registry import COMMANDS
from shape.bridge.spec import Command, options_schema

DRAFT = "https://json-schema.org/draft/2020-12/schema"
_ID: dict[str, Any] = {"type": ["string", "integer", "null"], "maxLength": 128}
_VERSION: dict[str, Any] = {
    "type": "string",
    "pattern": r"^(0|[1-9][0-9]{0,3})\.(0|[1-9][0-9]{0,3})$",
}
_WARNING: dict[str, Any] = {
    "type": "object",
    "properties": {"code": {"type": "string"}, "message": {"type": "string"}},
    "required": ["code", "message"],
    "additionalProperties": False,
}
ERROR: dict[str, Any] = {
    "type": "object",
    "properties": {
        "code": {"enum": sorted(ERROR_CODES)},
        "group": {"enum": list(GROUPS)},
        "message": {"type": "string"},
        "hint": {"type": ["string", "null"]},
    },
    "required": ["code", "group", "message", "hint"],
    "additionalProperties": False,
}


def _doc(title: str, body: dict[str, Any]) -> dict[str, Any]:
    return {"$schema": DRAFT, "title": title, "x-api-version": API_VERSION, **body}


def request_envelope() -> dict[str, Any]:
    return _doc(
        "bridge request",
        {
            "type": "object",
            "properties": {
                "api_version": _VERSION,
                "id": _ID,
                "command": {"enum": sorted(COMMANDS)},
                "args": {"type": "object"},
                "options": options_schema(),
            },
            "required": ["command"],
            "additionalProperties": False,
        },
    )


def response_envelope() -> dict[str, Any]:
    common = {
        "api_version": _VERSION,
        "id": _ID,
        "command": {"type": ["string", "null"]},
        "warnings": {"type": "array", "items": _WARNING},
    }
    return _doc(
        "bridge response",
        {
            "oneOf": [
                {
                    "type": "object",
                    "properties": {**common, "ok": {"const": True}, "result": {}},
                    "required": ["api_version", "id", "command", "ok", "result", "warnings"],
                    "additionalProperties": False,
                },
                {
                    "type": "object",
                    "properties": {**common, "ok": {"const": False}, "error": ERROR},
                    "required": ["api_version", "id", "command", "ok", "error", "warnings"],
                    "additionalProperties": False,
                },
            ]
        },
    )


def command_request(command: Command) -> dict[str, Any]:
    args = command.request_schema()
    required = ["command"] + (["args"] if args.get("required") else [])
    return _doc(
        f"{command.name} request",
        {
            "type": "object",
            "properties": {
                "api_version": _VERSION,
                "id": _ID,
                "command": {"const": command.name},
                "args": args,
                "options": options_schema(),
            },
            "required": required,
            "additionalProperties": False,
        },
    )


def command_result(command: Command) -> dict[str, Any]:
    return _doc(f"{command.name} result", dict(command.result))


def all_schemas() -> dict[str, dict[str, Any]]:
    """Every published schema, by its path under the schema directory."""
    files: dict[str, dict[str, Any]] = {
        "request.schema.json": request_envelope(),
        "response.schema.json": response_envelope(),
        "error.schema.json": _doc("bridge error", ERROR),
        "job.schema.json": _doc("bridge job", dict(JOB)),
    }
    listing: dict[str, Any] = {}
    for name, command in COMMANDS.items():
        files[f"commands/{name}.request.schema.json"] = command_request(command)
        files[f"commands/{name}.result.schema.json"] = command_result(command)
        listing[name] = {
            "summary": command.summary,
            "request": f"commands/{name}.request.schema.json",
            "result": f"commands/{name}.result.schema.json",
            "job": "always" if command.always_job else ("optional" if command.job else "never"),
            "cancellable": command.cancellable,
            "pending": command.pending,
        }
    files["index.json"] = {
        "format": "shape-bridge-schema-index",
        "version": 1,
        "api_version": API_VERSION,
        "commands": listing,
        "error_codes": dict(sorted(ERROR_CODES.items())),
    }
    return files


def render(doc: dict[str, Any]) -> str:
    return json.dumps(doc, indent=2, sort_keys=True) + "\n"


def write_schemas(directory: str | Path) -> list[Path]:
    root = Path(directory)
    written = []
    for rel, doc in all_schemas().items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render(doc), encoding="utf-8")
        written.append(target)
    return written


def check_schemas(directory: str | Path) -> list[str]:
    """What differs between the files in ``directory`` and the generated schemas (empty: none)."""
    root = Path(directory)
    problems = []
    expected = all_schemas()
    for rel, doc in expected.items():
        target = root / rel
        if not target.is_file():
            problems.append(f"missing: {rel}")
        elif target.read_text(encoding="utf-8") != render(doc):
            problems.append(f"changed: {rel}")
    if root.is_dir():
        have = {str(p.relative_to(root)).replace("\\", "/") for p in root.rglob("*.json")}
        problems.extend(f"unexpected: {rel}" for rel in sorted(have - set(expected)))
    return problems
