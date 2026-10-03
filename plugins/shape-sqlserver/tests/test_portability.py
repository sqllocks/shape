"""``shape profile-db --json`` writes the same bytes on every platform."""

from __future__ import annotations

import _pyio
import argparse
import builtins
import io
import json
import os

import pytest
from shape_sqlserver import ProfileDbCommand
from shape_sqlserver.testing import scenario

pytestmark = pytest.mark.contract


@pytest.fixture
def windows_text_io(monkeypatch: pytest.MonkeyPatch) -> None:
    """Text files behave as on Windows: the locale encoding is cp1252 and a text-mode write turns
    ``"\\n"`` into ``"\\r\\n"`` unless ``newline=`` says otherwise."""
    monkeypatch.setattr(os, "linesep", "\r\n")
    monkeypatch.setattr(_pyio.TextIOWrapper, "_get_locale_encoding", lambda self: "cp1252")
    monkeypatch.setattr(io, "open", _pyio.open)
    monkeypatch.setattr(builtins, "open", _pyio.open)


def test_json_summary_has_lf_line_ends_on_windows(monkeypatch, tmp_path, capsys, windows_text_io):
    monkeypatch.setattr("shape_sqlserver.profiler.connect", lambda text, creds: scenario("retail"))
    cmd = ProfileDbCommand()
    parser = argparse.ArgumentParser()
    cmd.configure(parser)
    summary = tmp_path / "s.json"
    args = parser.parse_args(["--connection-string", "Server=x", "--json", str(summary)])
    assert cmd.run(args) == 0
    raw = summary.read_bytes()
    assert raw.endswith(b"}\n") and b"\r" not in raw
    assert json.loads(raw)["tables"]
