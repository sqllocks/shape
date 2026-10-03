"""P6-11 deliverable 1: the versioned envelope, its errors, and the stdio loop."""

from __future__ import annotations

import io
import json
import sys

import pytest

from shape.bridge import API_VERSION, ERROR_CODES
from shape.bridge.protocol import BridgeError, group_of, parse_request
from shape.bridge.server import serve


def handle(bridge, request):
    return bridge.handle(request if isinstance(request, str) else json.dumps(request))


# ---- success and failure shapes -------------------------------------------------------------


def test_a_success_response_has_the_envelope(bridge):
    r = handle(bridge, {"api_version": "1.0", "id": "r-1", "command": "list"})
    assert set(r) == {"api_version", "id", "command", "ok", "result", "warnings"}
    assert (r["api_version"], r["id"], r["command"], r["ok"]) == (API_VERSION, "r-1", "list", True)
    assert r["warnings"] == [] and r["result"]["count"] >= 1


def test_a_failure_response_has_a_code_group_message_and_hint(bridge):
    r = handle(
        bridge, {"api_version": "1.0", "id": 7, "command": "describe", "args": {"domain": "nope"}}
    )
    assert set(r) == {"api_version", "id", "command", "ok", "error", "warnings"}
    assert r["ok"] is False and r["id"] == 7
    e = r["error"]
    assert set(e) == {"code", "group", "message", "hint"}
    assert e["code"] == "input.unknown_domain" and e["group"] == "input"
    assert "nope" in e["message"] and e["hint"]


@pytest.mark.parametrize("request_id", ["a", "", 0, 12345, None])
def test_the_id_is_echoed(bridge, request_id):
    r = handle(bridge, {"api_version": "1.0", "id": request_id, "command": "list"})
    assert r["id"] == request_id


@pytest.mark.parametrize("bad", [True, 1.5, [], {}, "x" * 129])
def test_a_bad_id_is_refused(bridge, bad):
    r = handle(bridge, {"api_version": "1.0", "id": bad, "command": "list"})
    assert not r["ok"] and r["error"]["code"] == "usage.invalid_request"


def test_an_error_still_echoes_the_id_of_the_request(bridge):
    r = handle(bridge, {"api_version": "1.0", "id": "keep", "command": "nope"})
    assert r["id"] == "keep" and r["command"] == "nope"
    assert r["error"]["code"] == "usage.unknown_command"
    assert "list" in r["error"]["hint"]


# ---- versions -------------------------------------------------------------------------------


def test_the_same_or_an_older_minor_is_served(bridge):
    assert handle(bridge, {"api_version": "1.0", "command": "list"})["ok"]


@pytest.mark.parametrize("version", ["2.0", "0.9", "3.1", "10.0"])
def test_an_incompatible_major_is_refused_naming_the_supported_range(bridge, version):
    r = handle(bridge, {"api_version": version, "id": "v", "command": "list"})
    assert r["ok"] is False and r["id"] == "v"
    assert r["error"]["code"] == "usage.unsupported_version"
    assert "1.0 to 1.0" in r["error"]["message"] and version in r["error"]["message"]
    assert r["api_version"] == API_VERSION  # the response always says what this bridge speaks


def test_a_newer_minor_is_served_with_a_warning(bridge):
    r = handle(bridge, {"api_version": "1.7", "command": "list"})
    assert r["ok"] and [w["code"] for w in r["warnings"]] == ["newer_minor_version"]


def test_a_missing_version_is_assumed_with_a_warning(bridge):
    r = handle(bridge, {"command": "list"})
    assert r["ok"] and [w["code"] for w in r["warnings"]] == ["api_version_assumed"]


@pytest.mark.parametrize("version", ["1", "1.0.0", "v1.0", 1.0, "", "1.-1", "01.0", None.__class__])
def test_a_malformed_version_is_refused(bridge, version):
    if version is None.__class__:
        version = []
    r = handle(bridge, {"api_version": version, "command": "list"})
    assert not r["ok"] and r["error"]["code"] == "usage.invalid_request"


# ---- malformed requests ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, code",
    [
        ("", "usage.invalid_json"),
        ("{", "usage.invalid_json"),
        ('{"command": "list"} trailing', "usage.invalid_json"),
        ('{"command": NaN}', "usage.invalid_json"),
        ('{"command": "list", "args": {"x": Infinity}}', "usage.invalid_json"),
        ("[]", "usage.invalid_request"),
        ('"list"', "usage.invalid_request"),
        ("null", "usage.invalid_request"),
        ('{"api_version": "1.0"}', "usage.invalid_request"),
        ('{"command": ""}', "usage.invalid_request"),
        ('{"command": 5}', "usage.invalid_request"),
        ('{"command": "list", "args": []}', "usage.invalid_request"),
        ('{"command": "list", "options": 3}', "usage.invalid_request"),
        ('{"command": "list", "params": {}}', "usage.invalid_request"),
    ],
)
def test_malformed_requests_get_a_usage_error(bridge, raw, code):
    r = bridge.handle(raw)
    assert r["ok"] is False and r["error"]["code"] == code and r["error"]["group"] == "usage"
    assert r["api_version"] == API_VERSION


def test_an_oversized_request_is_refused(bridge):
    r = bridge.handle(" " * (8 * 1024 * 1024 + 1))
    assert r["error"]["code"] == "usage.request_too_large"


def test_null_args_and_options_are_empty(bridge):
    assert handle(bridge, {"command": "list", "args": None, "options": None})["ok"]


# ---- arguments ------------------------------------------------------------------------------


def test_an_unknown_argument_is_refused_and_named(api):
    e = api.fail("describe", "usage.unknown_argument", domain="retail", colour="red")
    assert "colour" in e["message"] and "domain" in e["hint"]


def test_a_required_argument_is_needed(api):
    e = api.fail("describe", "usage.missing_argument")
    assert "'domain'" in e["message"]


@pytest.mark.parametrize(
    "command, args",
    [
        ("describe", {"domain": 5}),
        ("describe", {"domain": ""}),
        ("describe", {"domain": "retail", "mode": "snowflake"}),
        ("preview", {"domain": "retail", "rows": "5"}),
        ("preview", {"domain": "retail", "rows": True}),
        ("preview", {"domain": "retail", "rows": 0}),
        ("preview", {"domain": "retail", "tables": "customer"}),
        ("preview", {"domain": "retail", "tables": [1]}),
        ("generate", {"domain": "retail", "format": "xml"}),
        ("generate", {"domain": "retail", "seed": 1.5}),
        ("stream", {"domain": "retail", "interval_seconds": -1}),
        ("stream", {"domain": "retail", "interval_seconds": True}),
    ],
)
def test_a_wrongly_typed_or_out_of_range_argument_is_refused(api, command, args):
    r = api.call(command, **args)
    assert not r["ok"] and r["error"]["code"] == "usage.invalid_argument", r


def test_null_means_absent(api):
    assert api.ok("describe", domain="retail", mode=None, scale=None)["table_count"] == 9


def test_options_are_checked(api):
    api.fail("list", "usage.unknown_argument", options={"colour": 1})
    api.fail("list", "usage.invalid_argument", options={"include_raw_values": "yes"})
    api.fail("list", "usage.invalid_argument", options={"max_inline_bytes": 10})


def test_async_is_refused_for_a_command_that_is_not_a_job(api):
    e = api.fail("list", "usage.invalid_argument", options={"async": True})
    assert "async" in e["message"]


# ---- codes ----------------------------------------------------------------------------------


def test_every_code_is_grouped_and_documented():
    groups = {"usage", "input", "policy", "privacy", "io", "auth", "internal"}
    assert {group_of(c) for c in ERROR_CODES} == groups  # every group has a code
    for code, text in ERROR_CODES.items():
        assert group_of(code) in groups and text and code == code.lower()


def test_an_unregistered_code_cannot_be_raised():
    with pytest.raises(AssertionError):
        BridgeError("input.made_up", "x")


def test_an_internal_error_names_the_exception_and_keeps_the_traceback_out(api, monkeypatch):
    def boom(args, ctx):
        raise RuntimeError("kaboom")

    from shape.bridge.registry import COMMANDS

    monkeypatch.setitem(
        COMMANDS,
        "list",
        COMMANDS["list"].__class__(**{**COMMANDS["list"].__dict__, "handler": boom}),
    )
    r = api.call("list")
    assert r["error"]["code"] == "internal.error" and r["error"]["group"] == "internal"
    assert "RuntimeError: kaboom" in r["error"]["message"] and "Traceback" not in json.dumps(r)


@pytest.mark.parametrize(
    "exc, code",
    [
        (FileNotFoundError(2, "No such file", "/x/y"), "input.not_found"),
        (PermissionError(13, "Permission denied", "/x/y"), "io.read_failed"),
        (json.JSONDecodeError("bad", "{", 0), "input.invalid_schema"),
        (ValueError("bad value"), "input.invalid_value"),
        (KeyError("k"), "input.invalid_value"),
        (ImportError("needs extra"), "policy.capability_unavailable"),
        (NotImplementedError("later"), "policy.capability_unavailable"),
    ],
)
def test_exceptions_map_to_codes_by_type(exc, code):
    from shape.bridge.errors import to_bridge_error

    assert to_bridge_error(exc).code == code


def test_library_errors_map_to_their_groups():
    from shape.artifact.io import ArtifactSignatureError
    from shape.bridge.errors import to_bridge_error
    from shape.errors import ShapeSecurityError
    from shape.registry.local import RawProfileError
    from shape.scale.http import HttpError
    from shape.scale.sinks.base import SinkError

    assert to_bridge_error(ArtifactSignatureError("bad")).code == "policy.signature_invalid"
    assert to_bridge_error(ShapeSecurityError("no")).code == "policy.not_permitted"
    assert to_bridge_error(RawProfileError("raw")).code == "privacy.raw_values_withheld"
    assert to_bridge_error(HttpError(401, "https://x/y", "no")).code == "auth.rejected"
    assert to_bridge_error(HttpError(403, "https://x/y", "no")).code == "auth.rejected"
    assert to_bridge_error(HttpError(500, "https://x/y", "no")).code == "io.sink_failed"
    assert to_bridge_error(SinkError([("parquet", OSError("disk"))])).code == "io.sink_failed"


def test_parse_request_keeps_the_id_on_a_refusal():
    with pytest.raises(BridgeError) as info:
        parse_request({"id": "z", "command": "list", "bogus": 1})
    assert info.value.request_id == "z"


# ---- the stdio loop -------------------------------------------------------------------------


def run_loop(bridge, text, **kw):
    out = io.StringIO()
    code = serve(bridge, io.StringIO(text), out, **kw)
    return code, out.getvalue()


def test_one_response_line_per_request_line_in_order(bridge):
    lines = [
        json.dumps({"api_version": "1.0", "id": 1, "command": "list"}),
        "",
        "not json",
        json.dumps(
            {"api_version": "1.0", "id": 3, "command": "describe", "args": {"domain": "retail"}}
        ),
    ]
    code, text = run_loop(bridge, "\n".join(lines) + "\n")
    out = text.splitlines()
    assert code == 0 and len(out) == 3
    docs = [json.loads(x) for x in out]
    assert [d["id"] for d in docs] == [1, None, 3]
    assert [d["ok"] for d in docs] == [True, False, True]
    assert docs[1]["error"]["code"] == "usage.invalid_json"


def test_the_loop_survives_every_bad_line(bridge):
    bad = ['{"command": 1}', "[1]", "{", '{"command":"nope"}', '{"command":"describe"}']
    ok = json.dumps({"api_version": "1.0", "command": "list"})
    code, text = run_loop(bridge, "\n".join([*bad, ok]) + "\n")
    docs = [json.loads(x) for x in text.splitlines()]
    assert code == 0 and [d["ok"] for d in docs] == [False] * 5 + [True]


def test_an_oversized_line_is_refused_and_the_next_still_served(bridge, monkeypatch):
    monkeypatch.setattr("shape.bridge.server.MAX_REQUEST_BYTES", 64)
    big = '{"command": "list", "args": {"pad": "' + "x" * 500 + '"}}'
    code, text = run_loop(bridge, big + "\n" + '{"command": "list"}\n')
    docs = [json.loads(x) for x in text.splitlines()]
    assert docs[0]["error"]["code"] == "usage.request_too_large" and docs[1]["ok"]


def test_once_mode_reads_the_whole_input_as_one_request(bridge):
    pretty = json.dumps({"api_version": "1.0", "id": "p", "command": "list"}, indent=2)
    code, text = run_loop(bridge, pretty, once=True)
    assert code == 0 and len(text.splitlines()) == 1 and json.loads(text)["id"] == "p"


def test_once_mode_exit_codes(bridge):
    assert run_loop(bridge, '{"command": "nope"}', once=True)[0] == 1
    code, text = run_loop(bridge, "  \n", once=True)
    assert code == 1 and json.loads(text)["error"]["code"] == "usage.invalid_json"


def test_stdout_carries_only_responses(bridge, capsys, monkeypatch):
    from shape.bridge.registry import COMMANDS

    def noisy(args, ctx):
        print("stray output from a command")
        return {"version": "x", "domains": [], "count": 0}

    monkeypatch.setitem(
        COMMANDS,
        "list",
        COMMANDS["list"].__class__(**{**COMMANDS["list"].__dict__, "handler": noisy}),
    )
    out = io.StringIO()
    serve(bridge, io.StringIO('{"command": "list"}\n'), out)
    assert "stray" not in out.getvalue() and json.loads(out.getvalue())["ok"]
    assert "stray output from a command" in capsys.readouterr().err


def test_a_response_is_one_line_of_ascii_json(bridge):
    code, text = run_loop(bridge, '{"command": "describe", "args": {"domain": "retail"}}\n')
    assert text.count("\n") == 1 and text.isascii()


def test_importing_the_bridge_loads_no_heavy_module():
    import subprocess

    code = (
        "import sys, shape.bridge, shape.bridge.protocol, shape.cli.bridge;"
        "bad=[m for m in ('numpy','pyarrow','pandas') if m in sys.modules];"
        "print(','.join(bad)); sys.exit(1 if bad else 0)"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert done.returncode == 0, done.stdout
