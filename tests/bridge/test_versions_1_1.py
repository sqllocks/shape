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
from shape.bridge.spec import Arg, Command, check_args


def handle(bridge: Bridge, command: str, args=None, version="1.1", **extra):
    request = {"command": command, "args": args or {}, **extra}
    if version is not None:
        request["api_version"] = version
    return bridge.handle(json.dumps(request))


def test_the_bridge_speaks_1_2_and_serves_1_0_to_1_2():
    assert (API_MINOR, API_VERSION, SUPPORTED_RANGE) == (2, "1.2", "1.0 to 1.2")


def test_the_response_carries_the_bridges_own_version_whatever_the_request_declared(bridge):
    for version in ("1.0", "1.1", "1.2", "1.7", None):
        assert handle(bridge, "list", version=version)["api_version"] == "1.2"


def test_a_request_with_no_version_is_served_as_1_2_with_the_assumed_warning(bridge):
    r = handle(bridge, "list", version=None)
    assert r["ok"] and [w["code"] for w in r["warnings"]] == ["api_version_assumed"]
    assert "1.2" in r["warnings"][0]["message"]
    assert parse_request({"command": "list"}).minor == 2


def test_a_declared_minor_is_the_one_served_at_most_the_bridges(bridge):
    assert parse_request({"api_version": "1.0", "command": "list"}).minor == 0
    assert parse_request({"api_version": "1.1", "command": "list"}).minor == 1
    assert parse_request({"api_version": "1.2", "command": "list"}).minor == 2
    r = parse_request({"api_version": "1.9", "command": "list"})
    assert r.minor == 2 and [w["code"] for w in r.warnings] == ["newer_minor_version"]


def test_the_supported_range_is_named_in_the_refusal_of_another_major(bridge):
    r = handle(bridge, "list", version="2.0")
    assert r["error"]["code"] == "usage.unsupported_version"
    assert "1.0 to 1.2" in r["error"]["message"] and r["error"]["hint"] == "send api_version 1.2"


def test_every_command_and_argument_records_since_1_0_1_1_or_1_2():
    for name, command in COMMANDS.items():
        assert command.since in ("1.0", "1.1", "1.2"), name
        for arg_name, arg in command.args.items():
            assert arg.since in ("1.0", "1.1", "1.2"), (name, arg_name)
            if command.since != "1.0":
                assert arg.since == command.since, (name, arg_name)  # a new command: only new args


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


def test_a_1_0_request_cannot_use_a_1_1_command(bridge):
    new = [n for n, c in COMMANDS.items() if c.since == "1.1"]
    assert new
    for name in new:
        r = handle(bridge, name, version="1.0")
        assert r["error"]["code"] == "usage.unknown_command", name
        assert "api_version 1.1" in r["error"]["hint"], name
        listed = r["error"]["hint"].split("the commands are: ")[1].split(";")[0].split(", ")
        assert all(COMMANDS[c].since == "1.0" for c in listed)
        assert set(listed) == {n for n, c in COMMANDS.items() if c.since == "1.0"}


def test_a_1_1_request_can_use_a_1_1_command_and_the_same_unknown_name_is_still_unknown(bridge):
    r = handle(bridge, "no_such_command", version="1.1")
    assert r["error"]["code"] == "usage.unknown_command" and "api_version" not in r["error"]["hint"]
    assert "proposals_propose" in r["error"]["hint"]
    r = handle(bridge, "no_such_command", version="1.0")
    assert "proposals_propose" not in r["error"]["hint"]


def test_a_1_0_request_cannot_use_a_1_1_argument(bridge):
    added = [
        (n, a)
        for n, c in COMMANDS.items()
        if c.since == "1.0"
        for a, v in c.args.items()
        if v.since == "1.1"
    ]
    assert added
    for name, arg in added:
        r = handle(bridge, name, {arg: "x"}, version="1.0")
        assert r["error"]["code"] == "usage.unknown_argument", (name, arg)
        assert "api_version 1.1" in r["error"]["hint"], (name, arg)
        takes = r["error"]["hint"].split("it takes: ")[1].split(";")[0].split(", ")
        assert arg not in takes and all(COMMANDS[name].args[t].since == "1.0" for t in takes if t)


def test_a_1_1_argument_of_a_1_1_request_is_checked_not_refused():
    command = COMMANDS["profile"]
    args = {"source": "x.csv", "project": "shape.yml"}
    assert check_args(command, args, 1) == args
    with pytest.raises(Exception) as caught:
        check_args(command, args, 0)
    assert getattr(caught.value, "code", None) == "usage.unknown_argument"


def test_a_1_0_request_gets_no_1_1_field_and_no_new_warning(bridge, tmp_path, csv_pair):
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "format": "shape-verify-config",
                "version": 1,
                "ranges": {"a.amount": {"max": 1000}},
                "baseline": {"a": {"columns": {"id": "int64"}}},
            }
        )
    )
    args = {"path": str(csv_pair[0]), "config": str(config)}
    verify = handle(bridge, "verify", args, version="1.0")
    assert verify["ok"] and verify["warnings"] == []
    gates = verify["result"]["gates"]
    assert len(gates) >= 2  # the loop below is not vacuous
    assert all(set(g) == {"name", "passed", "errors", "warnings"} for g in gates)
    assert set(verify["result"]) == {"passed", "gates", "row_counts", "statistical"}
    new = handle(bridge, "verify", args, version="1.1")["result"]
    assert len(new["gates"]) == len(gates) and all("details" in g for g in new["gates"])
    out = tmp_path / "a.shape"
    profile = handle(
        bridge, "profile", {"source": str(csv_pair[0]), "output": str(out)}, version="1.0"
    )
    assert profile["ok"] and [w["code"] for w in profile["warnings"]] == [
        "profile_file_holds_values"
    ]
    diff = handle(bridge, "diff", {"before": str(out), "after": str(out)}, version="1.0")
    assert set(diff["result"]) == {"drifted", "change_count", "changes"}
    assert "project" not in diff["result"]


def test_a_1_0_request_with_a_1_1_value_only_is_the_1_0_error(bridge, csv_pair):
    r = handle(bridge, "verify", {"path": str(csv_pair[0]), "colour": "red"}, version="1.0")
    assert r["error"]["code"] == "usage.unknown_argument"
    assert r["error"]["hint"] == "it takes: config, format, path, schema, statistical, strict"
