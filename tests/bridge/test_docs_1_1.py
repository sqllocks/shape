"""W7-04 item 10: ``docs/BRIDGE.md`` documents every 1.1 command, and its examples are the
published vectors (so a documented example cannot drift from what the bridge answers)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from jsonschema_lite import validate

from shape.bridge.protocol import ERROR_CODES, WARNING_CODES
from shape.bridge.registry import COMMANDS

ROOT = Path(__file__).resolve().parents[2]
DOC = (ROOT / "docs" / "BRIDGE.md").read_text()
VECTORS = ROOT / "docs" / "bridge" / "vectors"
SCHEMAS = ROOT / "docs" / "bridge" / "schema"
NEW = sorted(n for n, c in COMMANDS.items() if c.since == "1.1")


def sections() -> dict[str, str]:
    """The text under each ``### `name` `` heading of the document."""
    parts = re.split(r"^### (.+)$", DOC, flags=re.M)
    return {parts[i].strip("` "): parts[i + 1] for i in range(1, len(parts) - 1, 2)}


def examples(kind: str) -> list[dict]:
    return [
        json.loads(m.group(1)) for m in re.finditer(rf"```json {kind}\n(.*?)\n```", DOC, flags=re.S)
    ]


def vector_cases() -> list[dict]:
    out = []
    for path in sorted(VECTORS.glob("*.json")):
        out.extend(json.loads(path.read_text())["cases"])
    return out


def worked(value):
    if isinstance(value, str):
        return value.replace("${DIR}", "/work")
    if isinstance(value, list):
        return [worked(v) for v in value]
    if isinstance(value, dict):
        return {k: worked(v) for k, v in value.items()}
    return value


def test_the_document_says_what_the_bridge_speaks():
    assert "this bridge speaks **1.1** and serves **1.0 to 1.1**" in DOC
    assert "the 1.0 promise" in DOC.lower() and "What's new in 1.1" in DOC
    assert "## Annotations for clients" in DOC and "x-path" in DOC and "effects" in DOC


@pytest.mark.parametrize("name", NEW)
def test_every_1_1_command_is_in_the_table_and_in_what_each_command_runs(name):
    rows = [line for line in DOC.splitlines() if line.startswith("|") and f"`{name}`" in line]
    assert len(rows) >= 2, f"{name} needs a row in the commands table and in the code table"
    table = next(r for r in rows if "(1.1" in r)
    for arg in COMMANDS[name].args:
        assert f"`{arg}`" in table, f"{name}: the table does not list {arg}"


@pytest.mark.parametrize("name", NEW)
def test_every_1_1_command_has_a_section_with_its_arguments_and_errors(name):
    found = sections()
    section = next((t for h, t in found.items() if h == name or h.startswith(f"{name} ")), None)
    if section is None:  # `format_schema` and the like are under a shared heading
        section = DOC[DOC.index(f"`{name}`") :]
    assert f"`{name}`" in DOC
    assert "Errors:" in section or "Error:" in section, name
    for arg in COMMANDS[name].args:
        assert f"`{arg}`" in section, f"{name}: the section does not describe {arg}"


def test_the_new_arguments_of_the_1_0_commands_are_described():
    section = DOC[DOC.index("### `project` and `source`") : DOC.index("## Schema design")]
    for command in ("profile", "diff", "check", "verify"):
        for arg, a in COMMANDS[command].args.items():
            if a.since == "1.1":
                assert f"`{arg}`" in section or f"`{arg}`" in DOC[DOC.index("## Comparison") :]
    assert "`details`" in DOC and "`enforced_passed`" in DOC and "`mode`" in DOC


def test_every_code_a_section_names_exists_and_the_new_ones_are_documented():
    named = set(re.findall(r"`((?:usage|input|policy|privacy|io|auth|internal)\.[a-z_]+)`", DOC))
    assert named <= set(ERROR_CODES), named - set(ERROR_CODES)
    for code in ("input.unknown_proposal", "input.unknown_source", "input.unknown_format"):
        assert f"`{code}`" in DOC
    warned = set(re.findall(r"`([a-z_]+)`", DOC)) & set(WARNING_CODES)
    assert {"project_source_not_selected", "artifact_not_verified", "result_in_file"} <= warned


def test_every_documented_example_is_a_published_vector():
    cases = vector_cases()
    requests = [worked(c["request"]) for c in cases]
    responses = [worked(c["response"]) for c in cases]
    documented_requests, documented_responses = examples("request"), examples("response")
    assert len(documented_requests) >= 12 and len(documented_requests) == len(documented_responses)
    for request in documented_requests:
        assert request in requests, request
    for response in documented_responses:
        assert response in responses, response


def test_every_documented_request_is_valid_for_the_published_schema_or_an_error_case():
    cases = {json.dumps(worked(c["request"]), sort_keys=True): c for c in vector_cases()}
    for request in examples("request"):
        case = cases[json.dumps(request, sort_keys=True)]
        schema = json.loads(
            (SCHEMAS / "commands" / f"{request['command']}.request.schema.json").read_text()
        )
        problems = validate(request, schema)
        assert (problems == []) == case.get("valid_request", True), (request, problems)


def test_the_documented_commands_have_a_success_and_a_failure_example_each():
    by_command: dict[str, set[bool]] = {}
    for request, response in zip(examples("request"), examples("response"), strict=True):
        assert response["command"] == request["command"] and response["id"] == request["id"]
        by_command.setdefault(request["command"], set()).add(response["ok"])
    for command in NEW:
        assert by_command.get(command), f"{command} has no example in the document"
    for command in ("proposals_propose", "proposals_decide", "project_validate", "design"):
        assert by_command[command] == {True, False}, command
