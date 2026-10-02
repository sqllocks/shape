"""The ``distribution`` strategy's log-normal column is drawn, clipped and rounded in one native
pass; the values are those of the step-by-step NumPy path, with either kernel."""

from __future__ import annotations

import numpy as np
import pyarrow as pa
import pytest

from shape.builtins.strategies.numeric import Distribution
from shape.generation import kernel_ops
from shape.generation.engine import EngineContext
from shape.generation.schema import Column
from shape.generation.strategy_kit import StrategyError


def _ctx(n: int, start: int = 0, scale: int | None = 2) -> EngineContext:
    column = Column(name="c", type="decimal", generator={}, scale=scale)
    return EngineContext(
        seed=1042, table="t", column="c", chunk=0, row_start=start, n_rows=n, column_def=column
    )


SPECS = [
    {"distribution": "log_normal", "mean": 4.0, "sigma": 0.8},
    {"distribution": "log_normal", "mean": 3.0, "sigma": 1.2, "min": 5, "max": 900},
    {"distribution": "log_normal", "params": {"mu": 6.0, "sigma": 0.3, "min": 1.5}},
    {"distribution": "log_normal", "mean": 2.0, "sigma": 0.5, "max": 40.25},
    {"distribution": "log_normal"},
]


def _plain(spec, ctx, monkeypatch):
    """The same column with the fused call declined: NumPy does each step."""
    with monkeypatch.context() as m:
        m.setattr(kernel_ops, "lognormal", lambda *a, **k: None)
        return Distribution().generate(spec, ctx)


@pytest.mark.parametrize("spec", SPECS, ids=lambda s: str(s)[:50])
@pytest.mark.parametrize("scale", [None, 0, 1, 2, 6])
@pytest.mark.parametrize("n", [0, 1, 300, 50_000])
def test_fused_log_normal_equals_the_numpy_steps(spec, scale, n, monkeypatch):
    ctx = _ctx(n, start=17, scale=scale)
    fused = Distribution().generate(spec, ctx)
    plain = _plain(spec, ctx, monkeypatch)
    assert fused.type == pa.float64() and len(fused) == n
    assert np.array_equal(
        np.asarray(fused.to_numpy(zero_copy_only=False)).view(np.uint64),
        np.asarray(plain.to_numpy(zero_copy_only=False)).view(np.uint64),
    )


def test_a_scale_the_kernel_cannot_round_still_works(monkeypatch):
    ctx = _ctx(200, scale=-1)  # round to tens: the NumPy route
    spec = {"distribution": "log_normal", "mean": 5.0, "sigma": 0.5}
    got = Distribution().generate(spec, ctx).to_numpy(zero_copy_only=False)
    assert (got % 10 == 0).all()


def test_bad_parameters_are_still_a_strategy_error():
    with pytest.raises(StrategyError):
        Distribution().generate({"distribution": "log_normal", "mean": 1.0, "sigma": -1.0}, _ctx(5))
