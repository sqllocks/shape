"""The `demo_*` commands call the functions of `shape.demo.api`, as `shape demo` does (#541)."""

from __future__ import annotations

import pytest

from shape.bridge.registry import COMMANDS

DEMO = ("demo_list", "demo_run", "demo_status", "demo_cleanup")


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("SHAPE_HOME", str(tmp_path / "home"))


def _run(api, **args):
    return api.ok("demo_run", scenario="retail", rows=60, seed=1, **args)


def test_demo_list_gives_what_the_api_gives(api):
    from shape.demo.api import demo_list

    result = api.ok("demo_list")
    assert result == demo_list() and result["count"] == len(result["scenarios"]) > 0
    assert "retail" in {s["name"] for s in result["scenarios"]}


def test_a_dry_run_plans_and_leaves_no_artifact(api):
    result = _run(api, dry_run=True)
    assert result["success"] and result["scenario"] == "retail" and result["mode"] == "inference"
    assert result["artifact_count"] == 0 and result["error"] is None


def test_a_demo_is_run_asked_after_and_cleaned_up(api):
    ran = _run(api, output_formats=["terminal"])
    assert ran["success"] and ran["artifact_count"] > 0
    assert ran["fidelity_score"] is None or 0.0 <= ran["fidelity_score"] <= 1.0
    session = ran["session_id"]
    status = api.ok("demo_status", session_id=session)
    assert status["session_id"] == session
    assert status["manifest"]["scenario"] == "retail" and status["manifest"]["success"] is True
    assert "fabric" not in status
    planned = api.ok("demo_cleanup", session_id=session, dry_run=True)
    assert planned["session_id"] == session and planned["dry_run"] is True
    done = api.ok("demo_cleanup", session_id=session)
    assert done["session_id"] == session and done["dry_run"] is False
    assert isinstance(done["removed"], list)


@pytest.mark.parametrize("dry_run", [False, True])
def test_a_demo_run_writes_nothing_to_standard_output(api, capsys, dry_run):
    capsys.readouterr()
    _run(api, output_formats=["terminal"], dry_run=dry_run)
    out, err = capsys.readouterr()
    assert out == "" and err  # progress goes to standard error: standard output is the reply


@pytest.mark.parametrize(
    "command, args",
    [
        ("demo_status", {"session_id": "no-such-session"}),
        ("demo_cleanup", {"session_id": "no-such-session"}),
        ("demo_run", {"scenario": "no-such-scenario"}),
        ("demo_run", {"mode": "no-such-mode"}),
    ],
)
def test_what_the_demo_cannot_use_is_an_input_error(api, command, args):
    error = api.fail(command, "input.invalid_value", **args)
    assert error["group"] == "input" and error["message"]


def test_a_spark_status_without_a_token_is_refused(api, monkeypatch):
    from shape.demo.manifest import DemoManifest

    monkeypatch.delenv("SHAPE_FABRIC_TOKEN", raising=False)
    DemoManifest(
        session_id="spark-1",
        scenario="retail",
        mode="inference",
        fabric_run_id="run-1",
        workspace_id="ws",
        notebook_item_id="nb",
    ).save()
    api.fail("demo_status", "input.invalid_value", session_id="spark-1")


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


def test_no_command_is_marked_pending():
    assert [name for name, c in COMMANDS.items() if c.pending] == []


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
