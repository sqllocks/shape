"""W6-01 deliverable 7: webhook notifications (``notifications:`` in shape.yml, ``--notify``)."""

from __future__ import annotations

import hashlib
import hmac
import http.client
import json
import shutil
import socket
from pathlib import Path

import pytest
from w6_stub import Receiver

from shape.cli import notify
from shape.cli.findings import Finding
from shape.cli.main import main
from shape.project import ProjectError, load_project
from shape.project.file import problems

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
URL_VAR = "SHAPE_TEST_HOOK"
SECRET_VAR = "SHAPE_TEST_SECRET"
SECRET = "s3cret-signing-key"


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr(notify, "sleep", slept.append)
    return slept


@pytest.fixture
def hook(monkeypatch):
    def start(*statuses: int) -> Receiver:
        rec = Receiver()
        rec.state.statuses = list(statuses) or [200]
        monkeypatch.setenv(URL_VAR, rec.url)
        monkeypatch.setenv(SECRET_VAR, SECRET)
        return rec

    return start


def _project(tmp_path: Path, which: str, notifications: str = "") -> Path:
    """A copy of a fixture project (`pass` matches its baseline, `fail` drifted) with a current
    profile in it and the given `notifications:` block."""
    root = tmp_path / "proj"
    shutil.copytree(FIXTURES / "action" / which, root)
    (root / "shape.yml").write_text(
        (root / "shape.yml").read_text(encoding="utf-8") + notifications, encoding="utf-8"
    )
    return root


def _diff(root: Path, monkeypatch, *extra: str) -> int:
    monkeypatch.chdir(root)
    assert main(["profile", "orders", "-o", "cur.shape"]) == 0
    return main(["diff", "--source", "orders", "cur.shape", *extra])


BLOCK_ALWAYS = f"notifications:\n  - url: env://{URL_VAR}\n    on: [always]\n"


# ---- the key of shape.yml ------------------------------------------------------------------


def _doc(notifications: object) -> dict:
    return {
        "format": "shape-project",
        "version": 1,
        "sources": {"orders": {"path": "data/orders.csv"}},
        "notifications": notifications,
    }


def test_a_project_without_the_key_validates_and_has_no_notifications(tmp_path):
    root = _project(tmp_path, "pass")
    project = load_project(root / "shape.yml")
    assert project.notifications == [] and "notifications" not in project.document


def test_the_frozen_v1_file_with_the_key_loads():
    project = load_project(FIXTURES / "project" / "v1_notifications" / "shape.yml")
    assert [n["url"] for n in project.notifications] == [
        "env://SHAPE_HOOK_URL",
        "file://secrets/chat-webhook",
    ]
    targets = notify.targets_of(project.notifications)
    assert targets[0].on == ("fail", "drift") and targets[0].secret == "env://SHAPE_HOOK_SECRET"
    assert targets[0].commands == ("diff", "check") and targets[1].commands is None


def test_the_schema_describes_the_key():
    from shape.project.file import schema

    entry = schema()["properties"]["notifications"]["items"]
    assert entry["required"] == ["url", "on"] and entry["additionalProperties"] is False
    assert set(entry["properties"]) == {"url", "on", "secret", "commands"}
    assert entry["properties"]["on"]["items"]["enum"] == ["fail", "drift", "always"]
    assert entry["properties"]["commands"]["items"]["enum"] == [
        "diff",
        "check",
        "verify",
        "fidelity",
    ]
    assert "notifications" not in schema()["required"]


@pytest.mark.parametrize(
    "entry",
    [
        {"url": "env://A", "on": ["fail"]},
        {"url": "file:///run/secrets/hook", "on": ["drift", "always"], "secret": "env://S"},
        {"url": "env://A", "on": ["fail"], "commands": ["verify", "fidelity"]},
    ],
)
def test_valid_entries(entry):
    assert problems(_doc([entry])) == []


@pytest.mark.parametrize(
    ("entry", "fragment"),
    [
        ({"on": ["fail"]}, "missing required key 'url'"),
        ({"url": "env://A"}, "missing required key 'on'"),
        ({"url": "https://hooks.example/abc", "on": ["fail"]}, "not a credential reference"),
        ({"url": "kv://vault/hook", "on": ["fail"]}, "not a credential reference"),
        ({"url": "env://", "on": ["fail"]}, "not a credential reference"),
        ({"url": "env://A", "on": ["fail"], "secret": "plain-text"}, "notifications[0].secret"),
        ({"url": "env://A", "on": []}, "list at least one"),
        ({"url": "env://A", "on": ["fail", "fail"]}, "once"),
        ({"url": "env://A", "on": ["sometimes"]}, "not in"),
        ({"url": "env://A", "on": ["fail"], "commands": []}, "list at least one"),
        ({"url": "env://A", "on": ["fail"], "commands": ["generate"]}, "not in"),
        ({"url": "env://A", "on": ["fail"], "commands": ["diff", "diff"]}, "once"),
        ({"url": "env://A", "on": ["fail"], "extra": 1}, "unexpected key 'extra'"),
        ({"url": "", "on": ["fail"]}, "notifications[0].url"),
    ],
)
def test_invalid_entries_are_reported_with_their_key_path(entry, fragment):
    found = problems(_doc([entry]))
    assert found and any(fragment in line for line in found), found
    assert any("notifications[0]" in line for line in found)


def test_the_key_must_be_a_list():
    assert problems(_doc({"url": "env://A"}))


def test_project_validate_shows_the_problem_and_exits_two(tmp_path, monkeypatch, capsys):
    root = _project(
        tmp_path, "pass", "notifications:\n  - url: https://example.test/h\n    on: [fail]\n"
    )
    monkeypatch.chdir(root)
    assert main(["project", "validate"]) == 2
    assert "notifications[0].url" in capsys.readouterr().err


def test_a_newer_project_version_is_still_refused(tmp_path):
    path = tmp_path / "shape.yml"
    path.write_text(
        "format: shape-project\nversion: 2\nsources:\n  a: {path: x}\nnotifications: []\n",
        encoding="utf-8",
    )
    with pytest.raises(ProjectError, match="newer"):
        load_project(path)


# ---- the document --------------------------------------------------------------------------


def _payload(**kw):
    base = {
        "command": "diff",
        "verdict": "fail",
        "exit_code": 1,
        "findings": [Finding("t", "c", "range_change", "high")],
        "project": "demo",
        "source": "orders",
    }
    return notify.payload(**{**base, **kw})


def test_the_payload_has_exactly_the_documented_keys():
    doc = _payload()
    assert set(doc) == {
        "format", "version", "command", "project", "source", "verdict", "exit_code", "counts",
        "findings", "shape_version", "time", "run_url",
    }  # fmt: skip
    assert doc["format"] == "shape-notification" and doc["version"] == 1
    assert doc["counts"] == {"findings": 1, "planned": 0}
    assert doc["findings"] == [
        {"table": "t", "column": "c", "kind": "range_change", "severity": "high"}
    ]
    from shape import __version__

    assert doc["shape_version"] == __version__
    assert doc["time"].endswith("Z") and len(doc["time"]) == 20


def test_run_url_comes_from_the_actions_environment_else_null():
    env = {
        "GITHUB_SERVER_URL": "https://github.com/",
        "GITHUB_REPOSITORY": "acme/data",
        "GITHUB_RUN_ID": "42",
    }
    assert notify.run_url(env) == "https://github.com/acme/data/actions/runs/42"
    assert notify.run_url({}) is None
    assert notify.run_url({**env, "GITHUB_RUN_ID": ""}) is None
    assert _payload(environ={})["run_url"] is None
    assert _payload(environ=env)["run_url"] == "https://github.com/acme/data/actions/runs/42"


def test_the_payload_lists_at_most_the_cap_and_counts_all():
    many = [Finding("t", f"c{i:04d}", "k", "low") for i in range(notify.MAX_LISTED + 50)]
    doc = _payload(findings=many)
    assert len(doc["findings"]) == notify.MAX_LISTED
    assert doc["counts"]["findings"] == notify.MAX_LISTED + 50


def test_the_body_is_canonical_json_of_the_document():
    doc = _payload()
    body = notify.body_of(doc)
    assert json.loads(body) == doc and body == notify.body_of(dict(reversed(list(doc.items()))))
    assert b"\n" not in body and b": " not in body


# ---- the signature -------------------------------------------------------------------------


def test_the_signature_is_the_hmac_sha256_of_the_exact_body():
    body = notify.body_of(_payload())
    expected = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    assert notify.sign(SECRET, body) == expected
    assert notify.verify(SECRET, body, expected)
    assert not notify.verify("another secret", body, expected)
    assert not notify.verify(SECRET, body + b" ", expected)
    assert not notify.verify(SECRET, body, "sha256=" + "0" * 64)
    assert not notify.verify(SECRET, body, expected.removeprefix("sha256="))


def test_the_header_verifies_with_the_secret_and_fails_with_another(hook, tmp_path, monkeypatch):
    with hook(200) as rec:
        root = _project(
            tmp_path,
            "fail",
            f"notifications:\n  - url: env://{URL_VAR}\n    on: [always]\n    secret: env://{SECRET_VAR}\n",
        )
        _diff(root, monkeypatch)
        (request,) = rec.state.requests
        header = request.headers[notify.SIGNATURE_HEADER]
        assert header.startswith("sha256=") and len(header) == 7 + 64
        assert notify.verify(SECRET, request.body, header)
        assert not notify.verify("a different secret", request.body, header)


def test_without_a_secret_there_is_no_signature_header(hook, tmp_path, monkeypatch):
    with hook(200) as rec:
        _diff(_project(tmp_path, "fail", BLOCK_ALWAYS), monkeypatch)
        (request,) = rec.state.requests
        assert notify.SIGNATURE_HEADER not in request.headers
        assert request.headers["Content-Type"] == "application/json"


# ---- delivery ------------------------------------------------------------------------------


def _target(url: str = f"env://{URL_VAR}") -> notify.Target:
    return notify.Target(url=url, on=("always",))


def test_a_2xx_response_is_delivered_at_once(hook):
    with hook(204) as rec:
        assert notify.deliver(_target(), b"{}") == 1
        assert len(rec.state.requests) == 1


def test_a_5xx_response_is_retried_with_backoff_then_succeeds(hook, no_sleep):
    with hook(503, 502, 200) as rec:
        assert notify.deliver(_target(), b"{}") == 3
        assert len(rec.state.requests) == 3 and no_sleep == [1.0, 2.0]


def test_three_attempts_at_most(hook, no_sleep):
    with hook(500) as rec:
        with pytest.raises(notify.DeliveryError, match="HTTP 500 after 3 attempts"):
            notify.deliver(_target(), b"{}")
        assert len(rec.state.requests) == 3 and no_sleep == [1.0, 2.0]


@pytest.mark.parametrize("status", [400, 401, 404, 410, 429])
def test_a_4xx_response_is_not_retried(hook, no_sleep, status):
    with hook(status) as rec:
        with pytest.raises(notify.DeliveryError, match=f"HTTP {status}"):
            notify.deliver(_target(), b"{}")
        assert len(rec.state.requests) == 1 and no_sleep == []


def test_a_redirect_is_neither_followed_nor_retried(hook, no_sleep):
    with hook(302) as rec:
        with pytest.raises(notify.DeliveryError, match="HTTP 302"):
            notify.deliver(_target(), b"{}")
        assert len(rec.state.requests) == 1 and no_sleep == []


def test_a_connection_error_is_retried(monkeypatch, no_sleep):
    with Receiver() as rec:
        url = rec.url
    monkeypatch.setenv(URL_VAR, url)  # nothing listens now
    with pytest.raises(notify.DeliveryError, match="connection failed .* after 3 attempts"):
        notify.deliver(_target(), b"{}")
    assert no_sleep == [1.0, 2.0]


def test_the_timeout_is_ten_seconds(monkeypatch, hook):
    seen: list[object] = []
    real = http.client.HTTPConnection.__init__

    def init(self, *a, **kw):
        seen.append(kw.get("timeout"))
        real(self, *a, **kw)

    monkeypatch.setattr(http.client.HTTPConnection, "__init__", init)
    with hook(200):
        notify.deliver(_target(), b"{}")
    assert seen == [10] == [notify.TIMEOUT]


@pytest.mark.parametrize(
    "url",
    [
        "http://hooks.example.test/x",
        "http://192.0.2.5/x",
        "ftp://127.0.0.1/x",
        "https://u:p@h.test/x",
    ],
)
def test_only_https_or_loopback_http(monkeypatch, url):
    monkeypatch.setenv(URL_VAR, url)

    def refuse(*a, **k):
        raise AssertionError("a connection was attempted")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    with pytest.raises(notify.DeliveryError) as exc:
        notify.deliver(_target(), b"{}")
    assert url not in str(exc.value) and "p@h" not in str(exc.value)
    assert "https://" in str(exc.value) or "credentials" in str(exc.value)


def test_an_unresolvable_reference_is_a_delivery_error_naming_the_reference(monkeypatch):
    monkeypatch.delenv(URL_VAR, raising=False)
    with pytest.raises(notify.DeliveryError, match=URL_VAR):
        notify.deliver(_target(), b"{}")


# ---- the commands --------------------------------------------------------------------------


def test_a_failed_delivery_warns_with_the_redacted_target_and_keeps_the_exit_code(
    hook, tmp_path, monkeypatch, capsys
):
    root = _project(tmp_path, "fail")
    plain = _diff(root, monkeypatch, "--fail-on-drift")
    capsys.readouterr()
    with hook(500) as rec:
        root2 = tmp_path / "again"
        shutil.copytree(root, root2)
        (root2 / "shape.yml").write_text(
            (root2 / "shape.yml").read_text(encoding="utf-8") + BLOCK_ALWAYS, encoding="utf-8"
        )
        code = _diff(root2, monkeypatch, "--fail-on-drift")
        cap = capsys.readouterr()
        assert code == plain == 1
        assert len(rec.state.requests) == 3
        assert "shape: warning: notification to" in cap.err
        assert f"notification to env://{URL_VAR} (http://127.0.0.1) failed" in cap.err
        assert "SECRETPATH123" not in cap.out + cap.err and rec.url not in cap.out + cap.err


def test_a_notification_does_not_change_a_passing_exit_code(hook, tmp_path, monkeypatch):
    with hook(404):
        assert _diff(_project(tmp_path, "pass", BLOCK_ALWAYS), monkeypatch) == 0


def test_the_webhook_url_never_appears_in_any_output(hook, tmp_path, monkeypatch, capsys):
    with hook(200) as rec:
        _diff(_project(tmp_path, "fail", BLOCK_ALWAYS), monkeypatch, "--json", "res.json")
        cap = capsys.readouterr()
        assert rec.url not in cap.out + cap.err and "SECRETPATH123" not in cap.out + cap.err
        assert "SECRETPATH123" not in (tmp_path / "proj" / "res.json").read_text(encoding="utf-8")


def test_the_document_carries_names_and_counts_but_no_data_value(hook, tmp_path, monkeypatch):
    with hook(200) as rec:
        _diff(_project(tmp_path, "fail", BLOCK_ALWAYS), monkeypatch)
        doc = json.loads(rec.state.requests[0].body)
        assert doc["format"] == "shape-notification" and doc["command"] == "diff"
        assert doc["verdict"] == "drift" and doc["exit_code"] == 0
        assert doc["project"] == "action-fixture" and doc["source"] == "orders"
        assert doc["counts"]["findings"] == len(doc["findings"]) > 0
        for f in doc["findings"]:
            assert set(f) == {"table", "column", "kind", "severity"}
        text = rec.state.requests[0].body.decode()
        data = (FIXTURES / "action" / "fail" / "data" / "orders.csv").read_text().splitlines()
        assert not any(row in text for row in data[1:20])
        assert '"baseline"' not in text and '"current"' not in text


@pytest.mark.parametrize(
    ("on", "which", "extra", "sent"),
    [
        ("[fail]", "fail", ["--fail-on-drift"], 1),
        ("[fail]", "fail", [], 0),  # drift reported, nothing failed
        ("[fail]", "pass", ["--fail-on-drift"], 0),
        ("[drift]", "fail", [], 1),
        ("[drift]", "fail", ["--fail-on-drift"], 1),  # findings, so drift, though the exit is 1
        ("[drift]", "pass", [], 0),
        ("[always]", "pass", [], 1),
        ("[always]", "fail", [], 1),
        ("[fail, drift]", "fail", ["--fail-on-drift"], 1),  # once, not twice
    ],
)
def test_on_selects_when_a_notification_is_sent(
    hook, tmp_path, monkeypatch, on, which, extra, sent
):
    with hook(200) as rec:
        block = f"notifications:\n  - url: env://{URL_VAR}\n    on: {on}\n"
        _diff(_project(tmp_path, which, block), monkeypatch, *extra)
        assert len(rec.state.requests) == sent


def test_the_commands_list_limits_which_commands_notify(hook, tmp_path, monkeypatch):
    with hook(200) as rec:
        block = (
            f"notifications:\n  - url: env://{URL_VAR}\n    on: [always]\n    commands: [check]\n"
        )
        _diff(_project(tmp_path, "fail", block), monkeypatch)
        assert rec.state.requests == []


def test_a_fail_verdict_carries_the_exit_code(hook, tmp_path, monkeypatch):
    with hook(200) as rec:
        block = f"notifications:\n  - url: env://{URL_VAR}\n    on: [fail]\n"
        _diff(_project(tmp_path, "fail", block), monkeypatch, "--fail-on-drift")
        doc = json.loads(rec.state.requests[0].body)
        assert doc["verdict"] == "fail" and doc["exit_code"] == 1


def test_notify_adds_a_target_for_one_run_and_repeats(hook, tmp_path, monkeypatch):
    with hook(200) as rec, Receiver() as other:
        monkeypatch.setenv("SHAPE_TEST_HOOK2", other.url)
        root = _project(tmp_path, "pass")
        _diff(
            root, monkeypatch, "--notify", f"env://{URL_VAR}", "--notify", "env://SHAPE_TEST_HOOK2"
        )
        assert len(rec.state.requests) == 1 and len(other.state.requests) == 1
        assert json.loads(rec.state.requests[0].body)["verdict"] == "pass"


def test_notify_works_without_a_project_and_with_json_output(hook, tmp_path, monkeypatch, capsys):
    from shape.cli.main import main as run

    with hook(200) as rec:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "a.csv").write_text("id,v\n" + "\n".join(f"{i},{i % 5}" for i in range(40)))
        assert run(["profile", "a.csv", "-o", "a.shape"]) == 0
        capsys.readouterr()
        assert (
            run(["diff", "a.shape", "a.shape", "--json", "-", "--notify", f"env://{URL_VAR}"]) == 0
        )
        assert json.loads(capsys.readouterr().out)["format"] == "shape-result"
        doc = json.loads(rec.state.requests[0].body)
        assert doc["project"] is None and doc["source"] is None and doc["verdict"] == "pass"


def test_a_bad_notify_value_is_a_usage_error_in_a_dry_run(tmp_path, monkeypatch, capsys):
    root = _project(tmp_path, "pass")
    monkeypatch.chdir(root)
    assert main(["profile", "orders", "-o", "cur.shape"]) == 0
    assert (
        main(
            ["diff", "--source", "orders", "cur.shape", "--notify", "https://x.test/h", "--dry-run"]
        )
        == 2
    )


def test_check_and_fidelity_have_the_flag_and_check_notifies(hook, tmp_path, monkeypatch):
    import argparse

    from shape.cli.introspect import core_commands

    flagged = {
        c.path
        for c in core_commands()
        if any("--notify" in a.option_strings for a in c.parser._actions)
    }
    assert flagged == {"diff", "check", "verify", "fidelity"}
    with hook(200) as rec:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "a.csv").write_text("id,v\n" + "\n".join(f"{i},{i % 5}" for i in range(40)))
        (tmp_path / "c.json").write_text(json.dumps({"columns": {"v": {}}}))
        assert main(["profile", "a.csv", "-o", "a.shape"]) == 0
        assert main(["check", "a.shape", "c.json", "--notify", f"env://{URL_VAR}"]) == 0
        doc = json.loads(rec.state.requests[0].body)
        assert doc["command"] == "check" and doc["verdict"] == "pass"
    assert isinstance(argparse.Namespace(), argparse.Namespace)


def test_fidelity_notifies_with_its_exit_code(hook, tmp_path, monkeypatch):
    with hook(200) as rec:
        monkeypatch.chdir(tmp_path)
        rows = "\n".join(f"{i},{i % 7}" for i in range(80))
        (tmp_path / "a.csv").write_text("id,v\n" + rows)
        (tmp_path / "b.csv").write_text("id,v\n" + rows)
        code = main(["fidelity", "a.csv", "b.csv", "--notify", f"env://{URL_VAR}"])
        doc = json.loads(rec.state.requests[0].body)
        assert doc["command"] == "fidelity" and doc["exit_code"] == code


def test_dry_run_lists_each_notification_as_a_send_action_and_opens_no_socket(
    hook, tmp_path, monkeypatch, capsys
):
    with hook(200) as rec:
        block = BLOCK_ALWAYS + "  - url: file://secrets/other-hook\n    on: [fail]\n"
        root = _project(tmp_path, "pass", block)
        monkeypatch.chdir(root)
        assert main(["profile", "orders", "-o", "cur.shape"]) == 0
        capsys.readouterr()

        def refuse(*a, **k):
            raise AssertionError("a socket was used during --dry-run")

        for name in ("connect", "connect_ex", "bind"):
            monkeypatch.setattr(socket.socket, name, refuse)
        monkeypatch.setattr(socket, "create_connection", refuse)
        monkeypatch.setattr(socket, "getaddrinfo", refuse)
        assert (
            main(
                [
                    "diff",
                    "--source",
                    "orders",
                    "cur.shape",
                    "--dry-run",
                    "--json",
                    "-",
                    "--notify",
                    "env://EXTRA",
                ]
            )
            == 0
        )
        doc = json.loads(capsys.readouterr().out)
        sends = [a for a in doc["actions"] if a["action"] == "send"]
        assert [a["target"] for a in sends] == [
            f"notification env://{URL_VAR}",
            "notification file://secrets/other-hook",
            "notification env://EXTRA",
        ]
        assert rec.state.requests == []
        assert main(["diff", "--source", "orders", "cur.shape", "--dry-run"]) == 0
        out = capsys.readouterr().out
        assert f"would send   notification env://{URL_VAR}" in out
        assert rec.url not in out and "SECRETPATH123" not in out


# ---- shape notify test ---------------------------------------------------------------------


def test_notify_test_sends_a_test_verdict_to_every_target(hook, tmp_path, monkeypatch, capsys):
    with hook(200) as rec, Receiver() as second:
        monkeypatch.setenv("SHAPE_TEST_HOOK2", second.url)
        block = (
            f"notifications:\n  - url: env://{URL_VAR}\n    on: [fail]\n    commands: [diff]\n"
            f"    secret: env://{SECRET_VAR}\n  - url: env://SHAPE_TEST_HOOK2\n    on: [drift]\n"
        )
        root = _project(tmp_path, "pass", block)
        monkeypatch.chdir(tmp_path)
        assert main(["notify", "test", "--project", str(root)]) == 0
        report = json.loads(capsys.readouterr().out)
        assert report["sent"] == 2 and report["failed"] == 0
        doc = json.loads(rec.state.requests[0].body)
        assert doc["format"] == "shape-notification" and doc["verdict"] == "test"
        assert doc["project"] == "action-fixture" and doc["findings"] == []
        assert notify.verify(
            SECRET,
            rec.state.requests[0].body,
            rec.state.requests[0].headers[notify.SIGNATURE_HEADER],
        )
        assert len(second.state.requests) == 1


def test_notify_test_exits_one_when_any_delivery_failed(hook, tmp_path, monkeypatch, capsys):
    with hook(200), Receiver() as bad:
        bad.state.statuses = [500]
        monkeypatch.setenv("SHAPE_TEST_HOOK2", bad.url)
        block = BLOCK_ALWAYS + "  - url: env://SHAPE_TEST_HOOK2\n    on: [always]\n"
        root = _project(tmp_path, "pass", block)
        assert main(["notify", "test", "--project", str(root)]) == 1
        cap = capsys.readouterr()
        report = json.loads(cap.out)
        assert report["sent"] == 1 and report["failed"] == 1
        assert bad.url not in cap.out + cap.err and "SECRETPATH123" not in cap.out + cap.err


def test_notify_test_searches_from_the_working_folder(hook, tmp_path, monkeypatch):
    with hook(200) as rec:
        root = _project(tmp_path, "pass", BLOCK_ALWAYS)
        monkeypatch.chdir(root)
        assert main(["notify", "test"]) == 0 and len(rec.state.requests) == 1


def test_notify_test_without_a_project_or_notifications_exits_two(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["notify", "test"]) == 2
    assert main(["notify", "test", "--project", str(tmp_path)]) == 2
    root = _project(tmp_path, "pass")
    assert main(["notify", "test", "--project", str(root)]) == 2
    assert "lists no notifications" in capsys.readouterr().err


# ---- compatibility -------------------------------------------------------------------------


def test_the_frozen_v1_payload_has_the_keys_a_current_document_has():
    frozen = json.loads((FIXTURES / "notify" / "v1" / "payload.json").read_text(encoding="utf-8"))
    assert frozen["format"] == "shape-notification" and frozen["version"] == 1
    assert isinstance(frozen["version"], int) and not isinstance(frozen["version"], bool)
    assert set(frozen) == set(_payload())
    assert set(frozen["counts"]) == {"findings", "planned"}
    assert all(set(f) == {"table", "column", "kind", "severity"} for f in frozen["findings"])
    # a receiver written against v1 reads it with nothing but these keys
    assert frozen["verdict"] in {"pass", "drift", "fail", "test"}
    assert frozen["time"].endswith("Z") and (
        frozen["run_url"] is None or frozen["run_url"].startswith("https://")
    )


def test_the_receiver_example_of_the_documentation_verifies_a_signature():
    body = (FIXTURES / "notify" / "v1" / "payload.json").read_bytes()
    header = notify.sign("topsecret", body)
    expected = "sha256=" + hmac.new(b"topsecret", body, hashlib.sha256).hexdigest()
    assert hmac.compare_digest(header, expected)
