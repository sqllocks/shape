"""W3-03: shared fixtures (the helpers are in ``history_helpers``)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))  # the tests run in importlib mode

from history_helpers import History, build_history, total_step  # noqa: E402


@pytest.fixture(scope="module")
def step_history(tmp_path_factory: pytest.TempPathFactory) -> History:
    """31 daily versions with a x1.4 step of ``orders.total`` on 2026-03-15."""
    return build_history(tmp_path_factory.mktemp("step"), 31, [total_step(14)])


@pytest.fixture(scope="module")
def amount_history(tmp_path_factory: pytest.TempPathFactory) -> History:
    """31 daily versions with a x1.3 step of the normal column ``orders.amount`` on 2026-03-15
    (a normal column has no heavy tail, so day-to-day sampling noise stays under the defaults)."""
    event = {
        "kind": "distribution",
        "table": "orders",
        "column": "amount",
        "start": 14,
        "scale": 1.3,
    }
    return build_history(tmp_path_factory.mktemp("amount"), 31, [event])
