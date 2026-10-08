"""Shared fixtures of the registry tests (the pruning helpers are in ``prune_helpers``)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))  # the tests run in importlib mode

from prune_helpers import Clock  # noqa: E402


@pytest.fixture()
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    """Sets the ``created_at`` of the next commit (``clock.at(moment)``)."""
    return Clock(monkeypatch)
