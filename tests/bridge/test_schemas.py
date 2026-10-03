"""P6-11 deliverable 6: a published JSON Schema for every request and result, so the schema cannot
change silently; and deliverable 1/2: every command of the plan is there."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema_lite import validate

from shape.bridge.protocol import API_VERSION, ERROR_CODES
from shape.bridge.registry import COMMANDS, PARITY_COMMANDS
from shape.bridge.schemas import all_schemas, check_schemas, command_request, render
from shape.cli.main import main

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "docs" / "bridge" / "schema"
PLAN_COMMANDS = {
    "list",
    "describe",
    "generate",
    "dry_run",
    "validate",
    "preview",
    "profile_info",
    "demo_list",
    "demo_run",
    "demo_status",
    "demo_cleanup",
    "scale_generate",
    "stream",
    "stream_status",
    "stream_stop",
    "scale_status",
    "scale_cancel",
}
ADDED = {"profile", "diff", "check", "verify"}
JOBS = {"job_status", "job_cancel", "job_list"}
#: The commands bridge 1.1 adds (their vectors, schemas and tests are in the 1.1 test files).
ADDED_1_1 = {
    "proposals_propose",
    "proposals_list",
    "proposals_decide",
    "project_validate",
    "project_show",
    "design",
    "design_from_data",
    "format_schema",
    "profile_show",
    "contract_validate",
    "safe_scan",
}


#: The commands bridge 1.2 adds (their vectors, schemas and tests are in the 1.2 test files).
ADDED_1_2 = {
    "proposals_contract",
}


def test_the_command_set_is_the_plans_plus_the_core_workflow_and_the_job_commands():
    assert set(COMMANDS) == PLAN_COMMANDS | ADDED | JOBS | ADDED_1_1 | ADDED_1_2
    assert set(PARITY_COMMANDS) == PLAN_COMMANDS and len(PARITY_COMMANDS) == 17


def test_the_committed_schemas_are_what_the_code_generates():
    assert check_schemas(SCHEMA_DIR) == [], "run: shape bridge schema --out docs/bridge/schema"


@pytest.mark.parametrize("name", sorted(COMMANDS))
def test_every_command_has_a_published_request_and_result_schema(name):
    request = json.loads((SCHEMA_DIR / "commands" / f"{name}.request.schema.json").read_text())
    result = json.loads((SCHEMA_DIR / "commands" / f"{name}.result.schema.json").read_text())
    assert (
        request["properties"]["command"] == {"const": name}
        and request["x-api-version"] == API_VERSION
    )
    assert request["additionalProperties"] is False and result["x-api-version"] == API_VERSION
    assert request["properties"]["args"]["additionalProperties"] is False
    required = [k for k, a in COMMANDS[name].args.items() if a.required]
    assert request["properties"]["args"].get("required", []) == required
    assert ("args" in request["required"]) == bool(required)
    sample = {k: (COMMANDS[name].args[k].enum or ("x",))[0] for k in required}
    assert not validate({"command": name, "args": sample}, request) or name in ("demo_run",)


def test_the_index_lists_every_command_and_error_code():
    index = json.loads((SCHEMA_DIR / "index.json").read_text())
    assert (
        index["api_version"] == API_VERSION
        and index["format"] == "shape-bridge-schema-index"
        and index["version"] == 1
    )
    assert set(index["commands"]) == set(COMMANDS) and index["error_codes"] == dict(
        sorted(ERROR_CODES.items())
    )
    for entry in index["commands"].values():
        assert (SCHEMA_DIR / entry["request"]).is_file() and (
            SCHEMA_DIR / entry["result"]
        ).is_file()
    assert (
        index["commands"]["stream"]["job"] == "always"
        and index["commands"]["generate"]["job"] == "optional"
    )
    assert (
        index["commands"]["list"]["job"] == "never"
        and index["commands"]["demo_run"]["pending"] == "P6-12"
    )
    assert (
        index["commands"]["scale_generate"]["cancellable"]
        and not index["commands"]["generate"]["cancellable"]
    )


def test_the_envelope_schemas_accept_what_the_bridge_sends(api):
    response_schema = json.loads((SCHEMA_DIR / "response.schema.json").read_text())
    request_schema = json.loads((SCHEMA_DIR / "request.schema.json").read_text())
    for command, args in (
        ("list", {}),
        ("describe", {"domain": "retail"}),
        ("describe", {"domain": "nope"}),
    ):
        request = {"api_version": "1.0", "id": "x", "command": command, "args": args}
        assert validate(request, request_schema) == []
        assert validate(api.bridge.handle(request), response_schema) == []
    assert validate({"command": "nope"}, request_schema)  # not a command of this version
    assert validate({"command": "list", "extra": 1}, request_schema)
    assert validate({"command": "list", "api_version": "one"}, request_schema)


def test_every_error_code_the_bridge_can_return_is_in_the_error_schema():
    error = json.loads((SCHEMA_DIR / "error.schema.json").read_text())
    assert set(error["properties"]["code"]["enum"]) == set(ERROR_CODES)
    ok = {"code": "usage.unknown_command", "group": "usage", "message": "m", "hint": None}
    assert validate(ok, error) == [] and validate({**ok, "code": "made.up"}, error)
    assert validate({**ok, "group": "nowhere"}, error) and validate(
        {k: v for k, v in ok.items() if k != "hint"}, error
    )


def test_a_changed_schema_is_reported(tmp_path):
    for rel, doc in all_schemas().items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(render(doc))
    assert check_schemas(tmp_path) == []
    target = tmp_path / "commands" / "describe.result.schema.json"
    changed = json.loads(target.read_text())
    changed["required"].remove("tables")
    target.write_text(json.dumps(changed))
    (tmp_path / "commands" / "list.request.schema.json").unlink()
    (tmp_path / "stray.json").write_text("{}")
    assert sorted(check_schemas(tmp_path)) == [
        "changed: commands/describe.result.schema.json",
        "missing: commands/list.request.schema.json",
        "unexpected: stray.json",
    ]


def test_the_request_schema_follows_the_argument_list():
    changed = command_request(COMMANDS["describe"])
    assert changed["properties"]["args"]["properties"]["mode"]["enum"] == ["3nf", "star"]
    assert changed["properties"]["args"]["required"] == ["domain"]


def test_the_cli_writes_and_checks_the_schemas(tmp_path, capsys):
    assert main(["bridge", "schema", "--out", str(tmp_path / "s")]) == 0
    assert "wrote" in capsys.readouterr().out
    assert main(["bridge", "schema", "--check", str(tmp_path / "s")]) == 0
    (tmp_path / "s" / "error.schema.json").write_text("{}")
    assert main(["bridge", "schema", "--check", str(tmp_path / "s")]) == 1
    assert "changed: error.schema.json" in capsys.readouterr().err
    assert main(["bridge", "schema", "--check", str(SCHEMA_DIR)]) == 0


@pytest.mark.parametrize("name", sorted(COMMANDS))
def test_each_command_has_a_summary_and_every_argument_a_description(name):
    command = COMMANDS[name]
    assert command.summary.endswith(".") and len(command.summary) > 10
    assert all(a.description for a in command.args.values())
