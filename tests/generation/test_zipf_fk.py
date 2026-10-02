"""P6-01-perf: the Zipf foreign key is drawn in one native pass (``kernel_ops.zipf_draw``) and gives
the rows ``zipf_index`` gives for the same uniforms, with either kernel."""

from __future__ import annotations

import numpy as np
import pytest

from shape.builtins.strategies import _relational
from shape.builtins.strategies._relational import ZIPF_HEAD, zipf_draw, zipf_index
from shape.generation.rng import RowStream
from shape.kernel import dispatch


@pytest.fixture(params=["rust", "python"])
def kernel(request, monkeypatch):
    monkeypatch.setenv("SHAPE_KERNEL", request.param)
    dispatch.reset()
    _relational._zipf_table.cache_clear()
    yield request.param
    dispatch.reset()
    _relational._zipf_table.cache_clear()


@pytest.mark.parametrize(
    ("pool", "alpha"), [(1, 1.5), (3, 1.0), (200, 1.5), (5_000, 1.2), (60_000, 0.9), (60_000, 2.5)]
)
@pytest.mark.parametrize("start", [0, 41_113])
def test_the_native_draw_equals_the_uniform_path(kernel, pool, alpha, start):
    stream = RowStream(1042, "order_line", "product_id", "fk")
    drawn = zipf_draw(stream, start, 40_000, pool, alpha)
    assert drawn is not None and drawn.dtype == np.int64
    expected = zipf_index(stream.uniform(start, 40_000), pool, alpha)
    assert np.array_equal(drawn, expected)
    assert drawn.min() >= 0 and drawn.max() < pool


def test_a_pool_beyond_the_exact_head_is_drawn_the_long_way(kernel, monkeypatch):
    monkeypatch.setattr(_relational, "ZIPF_HEAD", 100)
    stream = RowStream(1, "t", "c", "fk")
    assert zipf_draw(stream, 0, 10, 150, 1.5) is None
    assert zipf_draw(stream, 0, 10, 100, 1.5) is not None
    assert ZIPF_HEAD == 1 << 20
