"""BUGS-cli-1 #278: the crash path of the program redacts secrets like the expected-error path."""

from __future__ import annotations

import importlib

import pytest

cli_main = importlib.import_module("shape.cli.main")

SECRET_TEXT = "[08001] Login failed: Driver={ODBC Driver 18};Server=db;UID=sa;PWD=UNEXPECTED_SECRET"


def _run_as_program(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], exc: BaseException
) -> tuple[int, str]:
    def boom(argv: list[str]) -> int:
        raise exc

    monkeypatch.delenv("SHAPE_DEBUG", raising=False)
    monkeypatch.setattr(cli_main, "_dispatch", boom)
    monkeypatch.setattr("sys.argv", ["shape", "doctor"])
    code = cli_main.main()
    return code, capsys.readouterr().err


def test_unexpected_error_is_redacted(monkeypatch, capsys):
    code, err = _run_as_program(monkeypatch, capsys, RuntimeError(SECRET_TEXT))
    assert code == 2
    assert "UNEXPECTED_SECRET" not in err
    assert "PWD=***" in err
    assert err.startswith("shape: error: RuntimeError: ")
    assert "--debug" in err


def test_expected_error_still_redacted(monkeypatch, capsys):
    code, err = _run_as_program(monkeypatch, capsys, ValueError(SECRET_TEXT))
    assert code == 2
    assert "UNEXPECTED_SECRET" not in err
    assert "PWD=***" in err


def test_unexpected_error_without_detail(monkeypatch, capsys):
    code, err = _run_as_program(monkeypatch, capsys, RuntimeError())
    assert code == 2
    assert "RuntimeError: no detail" in err


def test_unexpected_error_plain_text_untouched(monkeypatch, capsys):
    code, err = _run_as_program(monkeypatch, capsys, RuntimeError("disk is on fire"))
    assert code == 2
    assert "disk is on fire" in err


def test_unexpected_error_bearer_token_redacted(monkeypatch, capsys):
    code, err = _run_as_program(
        monkeypatch, capsys, RuntimeError("401 for Authorization: Bearer abcdef0123456789abcdef")
    )
    assert "abcdef0123456789abcdef" not in err
