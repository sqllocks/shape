"""P1-01a: the native kernel, its Python twin, and the dispatcher (T-03)."""

from __future__ import annotations

import numpy as np
import pyarrow as pa
import pytest

import shape
from shape.kernel import dispatch, reference


def _big_batch(n: int = 1_000_000) -> pa.RecordBatch:
    rng = np.random.default_rng(7)
    cols = {
        "i64": pa.array(rng.integers(0, 10**9, n)),
        "f64": pa.array(rng.normal(size=n)),
        "i32": pa.array(rng.integers(0, 1000, n, dtype=np.int32)),
        "flag": pa.array(rng.random(n) < 0.5),
        "f32": pa.array(rng.random(n).astype(np.float32)),
        "with_nulls": pa.array(np.where(rng.random(n) < 0.1, None, rng.integers(0, 50, n))),
        "ts": pa.array(rng.integers(0, 10**15, n).astype("datetime64[us]")),
        "dt": pa.array(rng.integers(0, 20_000, n).astype("datetime64[D]")),
        "label": pa.array(rng.choice(["a", "bb", "ccc", None], n)),
        "u16": pa.array(rng.integers(0, 60_000, n, dtype=np.uint16)),
    }
    return pa.record_batch(cols)


def _addresses(batch: pa.RecordBatch) -> list[int]:
    out: list[int] = []
    for col in batch.columns:
        out.extend(b.address for b in col.buffers() if b is not None)
    return out


@pytest.fixture(autouse=True)
def _fresh_dispatch():
    dispatch.reset()
    yield
    dispatch.reset()


def test_active_kernel_matches_requested_mode():
    mode = dispatch.mode()
    name = dispatch.kernel_name()
    if mode == "rust":
        assert name == "rust"
    elif mode == "python":
        assert name == "python"
    else:
        assert name in {"rust", "python"}


def test_version_equals_package_version_in_both_implementations():
    native = dispatch._import_native()
    assert native.version() == reference.version() == shape.__version__


def test_zero_copy_roundtrip_1m_rows_10_columns():
    """Identical buffer addresses in and out: the batch crosses the boundary without a copy."""
    batch = _big_batch()
    assert batch.num_rows == 1_000_000 and batch.num_columns == 10
    kernel = dispatch.get_kernel()
    out = pa.record_batch(kernel.roundtrip_batch(batch))
    assert out.schema.equals(batch.schema)
    assert out.equals(batch)
    assert _addresses(out) == _addresses(batch)


def test_native_zero_copy_regardless_of_mode():
    native = dispatch._import_native()
    batch = _big_batch(100_000)
    out = pa.record_batch(native.roundtrip_batch(batch))
    assert _addresses(out) == _addresses(batch)


def test_native_and_reference_agree():
    native = dispatch._import_native()
    batch = _big_batch(50_000)
    assert native.num_rows(batch) == reference.num_rows(batch) == 50_000
    assert native.buffer_addresses(batch) == reference.buffer_addresses(batch) == _addresses(batch)
    assert pa.record_batch(native.roundtrip_batch(batch)).equals(
        pa.record_batch(reference.roundtrip_batch(batch))
    )


def test_mode_is_validated(monkeypatch):
    monkeypatch.setenv(dispatch.ENV_VAR, "fast")
    with pytest.raises(ValueError, match="SHAPE_KERNEL"):
        dispatch.get_kernel()


def test_python_mode_never_imports_native(monkeypatch):
    monkeypatch.setenv(dispatch.ENV_VAR, "python")

    def boom():
        raise AssertionError("native import attempted")

    monkeypatch.setattr(dispatch, "_import_native", boom)
    assert dispatch.get_kernel() is reference


def test_rust_mode_fails_loudly_and_auto_falls_back_without_the_extension(monkeypatch):
    def missing():
        raise ImportError("no extension")

    monkeypatch.setattr(dispatch, "_import_native", missing)
    monkeypatch.setenv(dispatch.ENV_VAR, "rust")
    with pytest.raises(ImportError, match="SHAPE_KERNEL=rust"):
        dispatch.get_kernel()
    dispatch.reset()
    monkeypatch.setenv(dispatch.ENV_VAR, "auto")
    assert dispatch.get_kernel() is reference


def test_kernel_is_reachable_as_an_attribute_of_the_package():
    """The wheel smoke test is `import shape; shape._kernel.version()`."""
    assert shape._kernel.version() == shape.__version__
    with pytest.raises(AttributeError):
        shape._not_a_thing  # noqa: B018
