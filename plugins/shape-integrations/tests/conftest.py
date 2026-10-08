"""Shared fixtures: a real run manifest with output files, and a way to hide a library."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from shape_integrations.testing import write_manifest


@pytest.fixture
def manifest(tmp_path: Path) -> Path:
    return write_manifest(tmp_path / "run")


@pytest.fixture
def hide_library(monkeypatch: pytest.MonkeyPatch):
    """``hide_library("mlflow")``: ``import mlflow`` raises ``ModuleNotFoundError`` in the test,
    whether or not the library is installed."""

    def hide(*names: str) -> None:
        for name in names:
            for loaded in [m for m in sys.modules if m == name or m.startswith(name + ".")]:
                monkeypatch.delitem(sys.modules, loaded)
            monkeypatch.setitem(sys.modules, name, None)

    return hide
