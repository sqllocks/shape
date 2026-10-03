"""Bridge 1.1: the version, and a request that declares 1.0 is answered as 1.0 answers it
(W7-04, item 1)."""

from __future__ import annotations

import json

import pytest

from shape.bridge.core import Bridge
from shape.bridge.protocol import (
    API_MINOR,
    API_VERSION,
    ERROR_CODES,
    SUPPORTED_RANGE,
    WARNING_CODES,
    parse_request,
    warning,
)
from shape.bridge.registry import COMMANDS
from shape.bridge.spec import Arg, Command


def handle(bridge: Bridge, command: str, args=None, version="1.1", **extra):
    request = {"command": command, "args": args or {}, **extra}
    if version is not None:
        request["api_version"] = version
    return bridge.handle(json.dumps(request))


def test_the_bridge_speaks_1_1_and_serves_1_0_to_1_1():
    assert (API_MINOR, API_VERSION, SUPPORTED_RANGE) == (1, "1.1", "1.0 to 1.1")


def test_the_response_carries_the_bridges_own_version_whatever_the_request_declared(bridge):
    for version in ("1.0", "1.1", "1.7", None):
        assert handle(bridge, "list", version=version)["api_version"] == "1.1"


def test_a_request_with_no_version_is_served_as_1_1_with_the_assumed_warning(bridge):
    r = handle(bridge, "list", version=None)
    assert r["ok"] and [w["code"] for w in r["warnings"]] == ["api_version_assumed"]
    assert "1.1" in r["warnings"][0]["message"]
    assert parse_request({"command": "list"}).minor == 1


def test_a_declared_minor_is_the_one_served_at_most_the_bridges(bridge):
    assert parse_request({"api_version": "1.0", "command": "list"}).minor == 0
    assert parse_request({"api_version": "1.1", "command": "list"}).minor == 1
    r = parse_request({"api_version": "1.9", "command": "list"})
    assert r.minor == 1 and [w["code"] for w in r.warnings] == ["newer_minor_version"]


def test_the_supported_range_is_named_in_the_refusal_of_another_major(bridge):
    r = handle(bridge, "list", version="2.0")
    assert r["error"]["code"] == "usage.unsupported_version"
    assert "1.0 to 1.1" in r["error"]["message"] and r["error"]["hint"] == "send api_version 1.1"


def test_every_command_and_argument_records_since_1_0_or_1_1():
    for name, command in COMMANDS.items():
        assert command.since in ("1.0", "1.1"), name
        for arg_name, arg in command.args.items():
            assert arg.since in ("1.0", "1.1"), (name, arg_name)
            if command.since == "1.1":
                assert arg.since == "1.1", (name, arg_name)  # a new command has only new args


def test_since_is_validated():
    with pytest.raises(AssertionError):
        Arg("string", "x", since="2.0")
    with pytest.raises(AssertionError):
        Command("c", "x.", {}, {}, lambda a, c: {}, since="1.9")
    with pytest.raises(AssertionError):
        Command("c", "x.", {}, {}, lambda a, c: {}, effects=("explodes",))
    with pytest.raises(AssertionError):
        Arg("string", "x", path="sideways")


def test_every_warning_and_error_code_is_registered():
    with pytest.raises(AssertionError):
        warning("made_up", "x")
    assert all(isinstance(m, str) and m for m in ERROR_CODES.values())
    assert all(isinstance(m, str) and m for m in WARNING_CODES.values())
