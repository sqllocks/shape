"""Fixtures for the fidelity-tier tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pyarrow as pa
import pytest

sys.path.insert(0, str(Path(__file__).parent))

from fid_helpers import make_table  # noqa: E402


@pytest.fixture
def table() -> pa.Table:
    return make_table(1)


@pytest.fixture
def other() -> pa.Table:
    return make_table(2)


@pytest.fixture
def no_sklearn(monkeypatch: pytest.MonkeyPatch) -> None:
    """``import sklearn`` (and its submodules) fails, whatever is installed."""
    for name in [m for m in sys.modules if m == "sklearn" or m.startswith("sklearn.")]:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.setitem(sys.modules, "sklearn", None)


@pytest.fixture
def no_scipy(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in [m for m in sys.modules if m == "scipy" or m.startswith("scipy.")]:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.setitem(sys.modules, "scipy", None)
