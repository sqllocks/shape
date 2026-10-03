"""P6-11 `demo_*` commands: specified and tested now; pending `shape demo` (P6-12), so they answer
`policy.capability_unavailable` once the request itself is valid."""

from __future__ import annotations

import pytest

from shape.bridge.registry import COMMANDS

DEMO = ("demo_list", "demo_run", "demo_status", "demo_cleanup")
VALID = {
    "demo_list": {},
    "demo_run": {
        "scenario": "retail",
        "mode": "inference",
        "rows": 100,
        "output_formats": ["csv"],
        "dry_run": True,
        "seed": 1,
    },
    "demo_status": {"session_id": "s-1"},
    "demo_cleanup": {"session_id": "s-1", "dry_run": True},
}


@pytest.mark.parametrize("command", DEMO)
def test_a_valid_demo_request_waits_for_shape_demo(api, command):
    e = api.fail(command, "policy.capability_unavailable", **VALID[command])
    assert "shape demo" in e["message"] and e["group"] == "policy" and e["hint"]


@pytest.mark.parametrize(
    "command, args, code",
    [
        ("demo_list", {"x": 1}, "usage.unknown_argument"),
        ("demo_run", {"rows": "100"}, "usage.invalid_argument"),
        ("demo_run", {"rows": 0}, "usage.invalid_argument"),
        ("demo_run", {"output_formats": "csv"}, "usage.invalid_argument"),
        ("demo_run", {"dry_run": "yes"}, "usage.invalid_argument"),
        ("demo_run", {"colour": "red"}, "usage.unknown_argument"),
        ("demo_status", {}, "usage.missing_argument"),
        ("demo_status", {"session_id": ""}, "usage.invalid_argument"),
        ("demo_status", {"session_id": 4}, "usage.invalid_argument"),
        ("demo_cleanup", {}, "usage.missing_argument"),
        ("demo_cleanup", {"session_id": "s", "dry_run": 1}, "usage.invalid_argument"),
    ],
)
def test_a_demo_request_is_checked_before_anything_else(api, command, args, code):
    api.fail(command, code, **args)


def test_the_demo_commands_are_marked_pending_in_the_command_table_and_nothing_else_is():
    pending = {name for name, c in COMMANDS.items() if c.pending}
    assert pending == set(DEMO) and all(COMMANDS[n].pending == "P6-12" for n in DEMO)


def test_the_demo_commands_take_the_arguments_of_the_original_protocol():
    assert set(COMMANDS["demo_run"].args) == {
        "scenario",
        "mode",
        "rows",
        "domain",
        "input_file",
        "connection",
        "output_formats",
        "dry_run",
        "seed",
        "scale_mode",
    }
    assert set(COMMANDS["demo_status"].args) == {"session_id", "token"}
    assert set(COMMANDS["demo_cleanup"].args) == {"session_id", "dry_run"}
    assert COMMANDS["demo_list"].args == {}
    assert COMMANDS["demo_status"].args["token"].secret
