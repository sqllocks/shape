"""W7-05 item 8: ``docs/BRIDGE.md`` documents every 1.2 command (its request and result, an example,
its error codes and warnings, what it calls) and ``docs/REGISTRY.md`` the drift between safe forms;
the examples are the published vectors, so a documented example cannot drift from what the bridge
answers."""

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
REGISTRY_DOC = (ROOT / "docs" / "REGISTRY.md").read_text()
VECTORS = ROOT / "docs" / "bridge" / "vectors"
SCHEMAS = ROOT / "docs" / "bridge" / "schema"
NEW = sorted(n for n, c in COMMANDS.items() if c.since == "1.2")


def section_of(name: str) -> str:
    parts = re.split(r"^### (.+)$", DOC, flags=re.M)
    found = {parts[i].strip("` "): parts[i + 1] for i in range(1, len(parts) - 1, 2)}
    return found[name]


def examples(kind: str) -> list[dict]:
    return [
        json.loads(m.group(1)) for m in re.finditer(rf"```json {kind}\n(.*?)\n```", DOC, flags=re.S)
    ]


def worked(value):
    if isinstance(value, str):
        return value.replace("${DIR}", "/work")
    if isinstance(value, list):
        return [worked(v) for v in value]
    if isinstance(value, dict):
        return {k: worked(v) for k, v in value.items()}
    return value


def vector_cases() -> list[dict]:
    out = []
    for path in sorted(VECTORS.glob("*.json")):
        out.extend(json.loads(path.read_text())["cases"])
    return out


def test_the_document_has_a_whats_new_in_1_2_and_says_what_the_bridge_speaks():
    assert "this bridge speaks **1.2** and serves **1.0 to 1.2**" in DOC
    assert "## What's new in 1.2" in DOC and "The 1.1 promise" in DOC
    assert "docs/bridge/schema/1.1/" in DOC and "test_compat_1_1.py" in DOC
    for name in NEW:
        assert f"`{name}`" in DOC.split("## What's new in 1.2")[1].split("## Report card")[0], name


@pytest.mark.parametrize("name", NEW)
def test_every_1_2_command_is_in_the_table_and_in_what_each_command_runs(name):
    rows = [line for line in DOC.splitlines() if line.startswith("|") and f"`{name}`" in line]
    assert len(rows) >= 2, f"{name} needs a row in the commands table and in the code table"
    table = next(r for r in rows if "(1.2" in r or "1.2" in r)
    for arg in COMMANDS[name].args:
        assert f"`{arg}`" in table, f"{name}: the table does not list {arg}"


@pytest.mark.parametrize("name", NEW)
def test_every_1_2_command_has_a_section_with_its_arguments_and_errors(name):
    section = section_of(name)
    assert "Errors:" in section, name
    for arg in COMMANDS[name].args:
        assert f"`{arg}`" in section, f"{name}: the section does not describe {arg}"


def test_every_code_a_section_names_exists_and_the_new_ones_are_documented():
    named = set(re.findall(r"`((?:usage|input|policy|privacy|io|auth|internal)\.[a-z_]+)`", DOC))
    assert named <= set(ERROR_CODES), named - set(ERROR_CODES)
    for code in ("input.contract_conflict", "policy.unverified_input"):
        assert f"`{code}`" in DOC
    warned = set(re.findall(r"`([a-z_]+)`", DOC)) & set(WARNING_CODES)
    assert {"real_input_corrupted", "result_in_file"} <= warned


def test_every_documented_example_is_a_published_vector():
    cases = vector_cases()
    requests = [worked(c["request"]) for c in cases]
    responses = [worked(c["response"]) for c in cases]
    documented_requests, documented_responses = examples("request"), examples("response")
    assert len(documented_requests) >= 30
    assert len(documented_requests) == len(documented_responses)
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


def test_the_documented_1_2_commands_have_a_success_and_a_failure_example_each():
    by_command: dict[str, set[bool]] = {}
    for request, response in zip(examples("request"), examples("response"), strict=True):
        assert response["command"] == request["command"] and response["id"] == request["id"]
        by_command.setdefault(request["command"], set()).add(response["ok"])
    for command in NEW:
        assert by_command.get(command) == {True, False}, command


def test_every_documented_response_says_the_version_of_the_bridge():
    for response in examples("response"):
        assert response["api_version"] == "1.2", response["id"]


def test_the_registry_document_explains_drift_between_safe_forms_and_not_measured():
    assert "## Drift between two safe forms" in REGISTRY_DOC
    for needle in ("not_measured", "range", "outlier_rate", "`registry_diff`", "same `kind`"):
        assert needle in REGISTRY_DOC, needle
    assert "two **share-safe profiles**" in REGISTRY_DOC


def test_the_changelog_has_an_entry_for_bridge_1_2():
    changelog = (ROOT / "CHANGELOG.md").read_text()
    assert "Bridge API 1.2" in changelog and "W7-05" in changelog
    for name in NEW:
        assert f"`{name}`" in changelog, name
