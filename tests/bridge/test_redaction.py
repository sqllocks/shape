"""AUD-security2: credentials never reach a bridge response, its log or a job file."""

from __future__ import annotations

import logging

SECRET = "BRIDGE_SECRET_X"
CONNECTION = f"Server=db;UID=sa;PWD={SECRET}"


def test_an_error_message_is_redacted(api):
    """#277: the CLI redacts this message; the bridge returned it as is."""
    response = api.call("generate", domain=CONNECTION)
    assert not response["ok"]
    assert SECRET not in str(response)
    assert "PWD=***" in response["error"]["message"]


def test_an_internal_error_is_redacted_in_the_response_and_the_log(api, monkeypatch, caplog):
    """#277: ``internal.error`` returned and logged ``str(exc)`` verbatim."""

    def boom() -> list[str]:
        raise RuntimeError(f"[08001] Login failed: Driver={{ODBC Driver 18}};{CONNECTION}")

    monkeypatch.setattr("shape.generation.domains.domain_names", boom)
    with caplog.at_level(logging.ERROR, logger="shape.bridge"):
        response = api.call("list")
    assert response["error"]["code"] == "internal.error"
    assert SECRET not in str(response)
    assert "RuntimeError" in response["error"]["message"]
    logged = "\n".join(caplog.messages + [caplog.text])
    assert "RuntimeError" in logged and SECRET not in logged


def test_a_warning_is_redacted(api, monkeypatch):
    """#277: a warning that quotes an exception (a domain that did not load) is redacted too."""
    import shape.generation.domains as domains

    real = domains.load_domain

    def load(name: str, *a, **k):
        raise ValueError(f"cannot reach {CONNECTION}")

    monkeypatch.setattr(domains, "load_domain", load)
    response = api.call("list")
    monkeypatch.setattr(domains, "load_domain", real)
    assert response["ok"] and response["warnings"]
    assert SECRET not in str(response)
