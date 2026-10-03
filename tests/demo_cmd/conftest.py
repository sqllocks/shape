"""Shared fixtures of the ``shape demo`` tests (P6-12): an isolated home and the command runner.
The helpers (a small schema file, a recording stand-in for the remote services) are in
``demo_helpers``."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

HERE = str(Path(__file__).resolve().parent)
if HERE not in sys.path:  # the suite runs with --import-mode=importlib
    sys.path.insert(0, HERE)

from demo_helpers import write_schema  # noqa: E402

from shape.cli.main import main  # noqa: E402


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    """Every test keeps its sessions, profiles and jobs in its own folder."""
    monkeypatch.setenv("SHAPE_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("SHAPE_JOBS_DIR", str(tmp_path / "jobs"))
    monkeypatch.delenv("SHAPE_DEBUG", raising=False)
    return tmp_path / "home"


@pytest.fixture
def run(capsys):
    def call(*argv: Any) -> tuple[int, str, str]:
        code = main([str(a) for a in argv])
        out = capsys.readouterr()
        return code, out.out, out.err

    return call


@pytest.fixture
def schema_file(tmp_path) -> Path:
    """A three-table domain (customer, order, order_line) with a ``small`` scale of ``ROWS``."""
    return write_schema(tmp_path / "shop.json")
