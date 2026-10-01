"""Multi-table profiling: BLAS scope for the correlation, order of work in the shared pool."""

from __future__ import annotations

import threading

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]
import pytest

import shape
from shape.profile.reference import _blas
from shape.profile.reference.readers import _Col
from shape.profile.reference.table import _col_work


def _blas_threads() -> int | None:
    control = _blas._find_control()
    return None if control is None else int(control[0]())


def test_single_thread_blas_restores_the_pool_size() -> None:
    before = _blas_threads()
    with _blas.single_thread_blas():
        if before is not None:
            assert _blas_threads() == 1
    assert _blas_threads() == before
    assert _blas._DEPTH == 0


def test_single_thread_blas_nests_and_is_shared_between_threads() -> None:
    before = _blas_threads()
    inside = threading.Event()
    release = threading.Event()
    seen: list[int | None] = []

    def other() -> None:
        with _blas.single_thread_blas():
            inside.set()
            release.wait(5)

    t = threading.Thread(target=other)
    t.start()
    inside.wait(5)
    with _blas.single_thread_blas():
        with _blas.single_thread_blas():
            seen.append(_blas_threads())
    # the other scope is still open: the pool must not have been restored yet
    seen.append(_blas_threads())
    release.set()
    t.join(5)
    if before is not None:
        assert seen == [1, 1]
    assert _blas_threads() == before


def test_single_thread_blas_restores_after_an_error() -> None:
    before = _blas_threads()
    with pytest.raises(RuntimeError), _blas.single_thread_blas():
        raise RuntimeError("boom")
    assert _blas_threads() == before
    assert _blas._DEPTH == 0


def test_single_thread_blas_without_openblas_is_a_no_op(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_blas, "_CONTROL", None)
    monkeypatch.setattr(_blas, "_PROBED", True)
    with _blas.single_thread_blas():
        pass
    assert _blas._DEPTH == 0


def test_col_work_orders_large_text_before_small_integers() -> None:
    arr = pa.chunked_array([pa.array([1, 2, 3])])
    big_text = _col_work(_Col("ts", "str", arr), 200_000)
    big_int = _col_work(_Col("id", "int", arr), 200_000)
    big_float = _col_work(_Col("amount", "float", arr), 200_000)
    small_text = _col_work(_Col("name", "str", arr), 20_000)
    assert big_float > big_text > big_int
    assert big_text > small_text


def test_correlation_is_the_same_with_blas_on_one_thread() -> None:
    rng = np.random.default_rng(7)
    n = 120_000
    tables = {
        "parent": pa.table({"parent_id": pa.array(np.arange(n)), "x": rng.normal(size=n)}),
        "child": pa.table(
            {
                "child_id": pa.array(np.arange(n)),
                "parent_id": pa.array(rng.integers(0, n, size=n)),
                "a": rng.normal(size=n),
                "b": pa.array(np.where(rng.random(n) < 0.1, None, rng.random(n)), pa.float64()),
            }
        ),
    }
    first = shape.profile(tables).to_dict()
    with _blas.single_thread_blas():
        second = shape.profile(tables).to_dict()
    assert first == second
