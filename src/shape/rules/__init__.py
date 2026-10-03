"""Rule mutation testing and rule backtesting (W3-01).

``mutation_test`` plants known faults (the corruptions of :mod:`shape.chaos.groundtruth`) and
reports which rules of a contract catch them. ``backtest`` replays a contract over a registry's
history. Names load on first use, so importing the package costs nothing.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .history import BacktestResult as BacktestResult
    from .history import backtest as backtest
    from .mutation import MutationResult as MutationResult
    from .mutation import mutation_test as mutation_test

_LAZY = {
    "mutation_test": "shape.rules.mutation",
    "MutationResult": "shape.rules.mutation",
    "backtest": "shape.rules.history",
    "BacktestResult": "shape.rules.history",
}
__all__ = sorted(_LAZY)


def __getattr__(name: str) -> Any:
    module = _LAZY.get(name)
    if module is None:
        raise AttributeError(f"module 'shape.rules' has no attribute {name!r}")
    return getattr(importlib.import_module(module), name)
