"""W6-01 deliverable 2: ``shape ci post-comment`` against a local HTTP stub (loopback only)."""

from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest
from w6_stub import TOKEN, Stub

from shape.cli import prbot
from shape.cli.main import main

BODY = f"{prbot.MARKER}\n## Shape data check\n\nbody one\n"


@pytest.fixture
def body_file(tmp_path: Path) -> Path:
    path = tmp_path / "comment.md"
    path.write_text(BODY, encoding="utf-8")
    return path


def _post(
    capsys, body_file: Path, *extra: str, repo: str = "acme/data", pr: int = 7
) -> tuple[int, str, str]:
    code = main(
        [
            "ci",
            "post-comment",
            "--body-file",
            str(body_file),
            "--repo",
            repo,
            "--pr",
            str(pr),
            *extra,
        ]
    )
    cap = capsys.readouterr()
    return code, cap.out, cap.err


@pytest.fixture
def env(monkeypatch):
    def setup(stub: Stub, token: str | None = TOKEN) -> None:
        monkeypatch.setenv("GITHUB_API_URL", stub.url)
        if token is None:
            monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        else:
            monkeypatch.setenv("GITHUB_TOKEN", token)

    return setup


def test_one_comment_is_created_then_updated_in_place(capsys, env, body_file):
    with Stub() as stub:
        env(stub)
        code, out, err = _post(capsys, body_file)
        assert code == 0 and json.loads(out)["action"] == "created"
        body_file.write_text(BODY.replace("one", "two"), encoding="utf-8")
        code, out, err = _post(capsys, body_file)
        assert code == 0 and json.loads(out)["action"] == "updated"
        _post(capsys, body_file)
        assert len(stub.state.comments) == 1
        assert "body two" in stub.state.comments[0]["body"]
        methods = [r.method for r in stub.state.requests if r.path.startswith("/repos")]
        assert methods.count("POST") == 1 and methods.count("PATCH") == 2
        posted = next(r for r in stub.state.requests if r.method == "POST")
        assert posted.path == "/repos/acme/data/issues/7/comments"
        patched = next(r for r in stub.state.requests if r.method == "PATCH")
        assert patched.path == f"/repos/acme/data/issues/comments/{stub.state.comments[0]['id']}"


def test_the_token_is_sent_only_as_a_bearer_header_and_never_printed(capsys, env, body_file):
    with Stub() as stub:
        env(stub)
        stub.state.forced.append(
            ("POST", "/repos", 500, json.dumps({"message": TOKEN}).encode(), {})
        )
        code, out, err = _post(capsys, body_file)
        assert code == 1
        every = out + err
        assert TOKEN not in every
        for r in stub.state.requests:
            assert r.headers["Authorization"] == f"Bearer {TOKEN}"
            assert TOKEN not in r.path and TOKEN.encode() not in r.body
        code, out, err = _post(capsys, body_file)
        assert TOKEN not in out + err


def test_a_comment_of_another_author_with_the_marker_is_not_taken_over(capsys, env, body_file):
    with Stub() as stub:
        env(stub)
        stub.state.comments.append({"id": 1, "user": {"login": "mallory"}, "body": BODY})
        assert _post(capsys, body_file)[0] == 0
        assert stub.state.comments[0]["body"] == BODY and len(stub.state.comments) == 2
        assert stub.state.comments[1]["user"]["login"] == "shape-bot"


def test_a_comment_of_the_author_without_the_marker_is_left_alone(capsys, env, body_file):
    with Stub() as stub:
        env(stub)
        stub.state.comments.append({"id": 1, "user": {"login": "shape-bot"}, "body": "hello"})
        assert _post(capsys, body_file)[0] == 0
        assert stub.state.comments[0]["body"] == "hello" and len(stub.state.comments) == 2


def test_an_installation_token_that_cannot_read_user_acts_as_the_actions_bot(
    capsys, env, body_file
):
    with Stub() as stub:
        env(stub)
        stub.state.login = None
        stub.state.author_of_new = "github-actions[bot]"
        assert _post(capsys, body_file)[0] == 0
        assert _post(capsys, body_file)[0] == 0
        assert len(stub.state.comments) == 1


def test_the_existing_comment_is_found_on_a_later_page(capsys, env, body_file):
    with Stub() as stub:
        env(stub)
        stub.state.comments += [
            {"id": i, "user": {"login": "someone"}, "body": "chat"} for i in range(1, 101)
        ]
        stub.state.comments.append({"id": 500, "user": {"login": "shape-bot"}, "body": BODY})
        assert _post(capsys, body_file)[0] == 0
        assert len(stub.state.comments) == 101 and stub.state.comments[-1]["id"] == 500


def test_an_enterprise_server_url_with_a_path_prefix_is_used(capsys, monkeypatch, body_file):
    with Stub(prefix="/api/v3") as stub:
        monkeypatch.setenv("GITHUB_API_URL", stub.url)
        monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
        assert _post(capsys, body_file)[0] == 0
        assert all(r.path.startswith("/api/v3/") for r in stub.state.requests)
        assert len(stub.state.comments) == 1


@pytest.mark.parametrize("status", [403, 404])
def test_a_token_that_cannot_write_prints_a_notice_and_exits_zero(capsys, env, body_file, status):
    with Stub() as stub:
        env(stub)
        stub.state.forced.append(("POST", "/repos", status, b'{"message":"x"}', {}))
        code, out, err = _post(capsys, body_file)
        assert code == 0
        assert "shape: notice:" in err and "fork" in err and str(status) in err
        assert json.loads(out) == {"posted": False, "status": status}
        assert TOKEN not in out + err


def test_a_forbidden_comment_list_also_exits_zero(capsys, env, body_file):
    with Stub() as stub:
        env(stub)
        stub.state.forced.append(("GET", "/repos", 403, b"{}", {}))
        assert _post(capsys, body_file)[0] == 0


@pytest.mark.parametrize("status", [400, 401, 422, 500, 503])
def test_other_http_errors_exit_one(capsys, env, body_file, status):
    with Stub() as stub:
        env(stub)
        stub.state.forced.append(("POST", "/repos", status, b"{}", {}))
        code, out, err = _post(capsys, body_file)
        assert code == 1 and f"HTTP {status}" in err and TOKEN not in err


def test_a_redirect_is_not_followed(capsys, env, body_file):
    with Stub() as stub:
        env(stub)
        stub.state.forced.append(
            ("POST", "/repos", 307, b"", {"Location": "/repos/elsewhere/issues/1/comments"})
        )
        code, out, err = _post(capsys, body_file)
        assert code == 1 and "redirect" in err
        assert not any("elsewhere" in r.path for r in stub.state.requests)
        assert stub.state.comments == []


def test_an_unreachable_server_exits_one_without_the_token(capsys, monkeypatch, body_file):
    with Stub() as stub:
        url = stub.url
    monkeypatch.setenv("GITHUB_API_URL", url)  # nothing listens any more
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    code, out, err = _post(capsys, body_file)
    assert code == 1 and "cannot reach" in err and TOKEN not in err


def test_no_token_exits_two_and_names_the_variable(capsys, env, body_file):
    with Stub() as stub:
        env(stub, token=None)
        code, out, err = _post(capsys, body_file)
        assert code == 2 and "GITHUB_TOKEN" in err and stub.state.requests == []


def test_the_token_cannot_be_given_as_an_argument(capsys, env, body_file):
    with Stub() as stub:
        env(stub)
        with pytest.raises(SystemExit) as exc:
            main(
                [
                    "ci",
                    "post-comment",
                    "--body-file",
                    str(body_file),
                    "--repo",
                    "a/b",
                    "--pr",
                    "1",
                    "--token",
                    TOKEN,
                ]
            )
        assert exc.value.code == 2


@pytest.mark.parametrize(
    "url",
    [
        "http://api.github.example/",
        "http://192.0.2.1/",
        "ftp://127.0.0.1/",
        "file:///etc/passwd",
        "https://user:pw@api.github.example/",
        "https:///nohost",
        "not a url",
    ],
)
def test_only_https_or_loopback_http_is_accepted(capsys, monkeypatch, body_file, url):
    monkeypatch.setenv("GITHUB_API_URL", url)
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)

    def refuse(*a, **k):
        raise AssertionError("a connection was attempted")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    code, out, err = _post(capsys, body_file)
    assert code == 2 and ("https://" in err or "credentials" in err)
    assert TOKEN not in out + err and "pw@" not in out + err


@pytest.mark.parametrize("host", ["http://localhost:1/", "http://127.0.0.1:1/", "http://[::1]:1/"])
def test_loopback_http_passes_the_url_check(host):
    assert prbot.check_api_url(host).hostname in ("localhost", "127.0.0.1", "::1")


def test_bad_arguments_exit_two_before_any_request(capsys, env, body_file, tmp_path):
    with Stub() as stub:
        env(stub)
        assert _post(capsys, body_file, repo="no-slash")[0] == 2
        assert _post(capsys, body_file, repo="a/b/c")[0] == 2
        assert _post(capsys, body_file, pr=0)[0] == 2
        assert _post(capsys, body_file, pr=-3)[0] == 2
        assert _post(capsys, tmp_path / "missing.md")[0] == 2
        no_marker = tmp_path / "nm.md"
        no_marker.write_text("just text", encoding="utf-8")
        assert _post(capsys, no_marker)[0] == 2
        big = tmp_path / "big.md"
        big.write_text(prbot.MARKER + "x" * prbot.MAX_BODY, encoding="utf-8")
        assert _post(capsys, big)[0] == 2
        assert stub.state.requests == []


def test_a_body_of_exactly_the_limit_is_accepted(capsys, env, tmp_path):
    with Stub() as stub:
        env(stub)
        edge = tmp_path / "edge.md"
        edge.write_text(prbot.MARKER + "x" * (prbot.MAX_BODY - len(prbot.MARKER)), encoding="utf-8")
        assert _post(capsys, edge)[0] == 0


def test_dry_run_prints_one_send_action_and_opens_no_socket(capsys, monkeypatch, body_file):
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    monkeypatch.setenv("GITHUB_API_URL", "https://ghe.example.test/api/v3")

    def refuse(*a, **k):
        raise AssertionError("a socket was used during --dry-run")

    for name in ("connect", "connect_ex", "bind"):
        monkeypatch.setattr(socket.socket, name, refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    code, out, err = _post(capsys, body_file, "--dry-run", "--json")
    assert code == 0
    doc = json.loads(out)
    assert doc["format"] == "shape-dry-run"
    assert doc["actions"] == [{"action": "send", "target": "github acme/data#7"}]
    assert TOKEN not in out + err and "ghe.example.test" not in out
    code, out, err = _post(capsys, body_file, "--dry-run")
    assert code == 0 and out.strip() == "would send   github acme/data#7"


def test_dry_run_still_rejects_bad_input(capsys, body_file, tmp_path):
    assert _post(capsys, tmp_path / "gone.md", "--dry-run")[0] == 2
    assert _post(capsys, body_file, "--dry-run", repo="bad")[0] == 2


def test_the_json_flag_wraps_the_outcome(capsys, env, body_file):
    with Stub() as stub:
        env(stub)
        code, out, err = _post(capsys, body_file, "--json")
        doc = json.loads(out)
        assert code == 0 and doc["format"] == "shape-result" and doc["command"] == "ci post-comment"
        assert doc["posted"] is True and doc["action"] == "created"
