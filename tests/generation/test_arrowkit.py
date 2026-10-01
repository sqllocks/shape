"""``shape.generation.arrowkit`` builds the arrays pyarrow would, and never imports pandas."""

from __future__ import annotations

import subprocess
import sys
import textwrap
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pytest

from shape.generation import arrowkit

RNG = np.random.default_rng(7)


def same(got: pa.Array, want: pa.Array) -> bool:
    """Equal type, nulls and values (NaN equals NaN)."""
    return (
        got.type == want.type
        and got.null_count == want.null_count
        and got.to_pylist().__repr__() == want.to_pylist().__repr__()
    )


NUMPY_INPUTS: list[Any] = [
    np.arange(10, dtype=np.int64),
    np.arange(10, dtype=np.int32),
    np.arange(10, dtype=np.uint8),
    RNG.random(9),
    RNG.random(9).astype(np.float32),
    RNG.random(9) > 0.5,
    np.arange(13) % 3 == 0,
    np.array(["2024-01-01", "2024-02-03", "NaT"], dtype="datetime64[us]"),
    np.array(["2024-01-01", "2024-02-03"], dtype="datetime64[D]"),
    np.array(["2024-01-01", "2024-02-03"], dtype="datetime64[ns]"),
    np.arange(20, dtype=np.int64)[::3],  # not contiguous
    np.array([], dtype=np.int64),
    np.array([1.0, np.nan, 3.0]),
    np.array(["a", "bb", "ccc"]),  # a dtype pyarrow decides on
]


@pytest.mark.parametrize("values", NUMPY_INPUTS, ids=lambda v: f"{v.dtype}-{len(v)}")
@pytest.mark.parametrize("from_pandas", [False, True])
def test_array_matches_pyarrow(values: Any, from_pandas: bool) -> None:
    assert same(
        arrowkit.array(values, from_pandas=from_pandas), pa.array(values, from_pandas=from_pandas)
    )


@pytest.mark.parametrize("values", NUMPY_INPUTS[:8], ids=lambda v: f"{v.dtype}-{len(v)}")
def test_array_with_mask_matches_pyarrow(values: Any) -> None:
    mask = np.arange(len(values)) % 2 == 0
    got = arrowkit.array(values, mask=mask)
    want = pa.array(values, mask=mask)
    assert same(got, want)


def test_array_type_and_other_inputs() -> None:
    ints = np.arange(5, dtype=np.int64)
    assert arrowkit.array(ints, type=pa.int64()).equals(pa.array(ints, type=pa.int64()))
    assert arrowkit.array(ints, type=pa.float64()).equals(pa.array(ints, type=pa.float64()))
    already = pa.array([1, 2, 3])
    assert arrowkit.array(already) is already
    assert arrowkit.array(already, type=pa.int32()).equals(pa.array(already, type=pa.int32()))
    assert arrowkit.array(["é", "", "abc"]).equals(pa.array(["é", "", "abc"]))
    assert arrowkit.array(["x", "y"], type=pa.string()).equals(pa.array(["x", "y"]))
    assert arrowkit.array(["x", None]).equals(pa.array(["x", None]))
    assert arrowkit.array([]).equals(pa.array([]))
    assert arrowkit.array((0.5, 0.25), type=pa.float64()).equals(pa.array([0.5, 0.25]))
    assert arrowkit.array([1, 2, 3]).equals(pa.array([1, 2, 3]))


def _arrays() -> list[pa.Array]:
    return [
        pa.array(np.arange(7, dtype=np.int64)),
        pa.array(np.arange(7, dtype=np.int32)),
        pa.array(RNG.random(7)),
        pa.array(np.arange(70) % 3 == 0),
        pa.array(np.array(["2024-01-01", "2024-02-03", "2025-05-05"], dtype="datetime64[us]")),
        pa.array(np.array(["2024-01-01", "2024-02-03"], dtype="datetime64[D]")),
        pa.array([1, None, 3]),
        pa.array([1.5, None, 3.5]),
        pa.array([True, None, False]),
        pa.array(np.array(["2024-01-01", "NaT"], dtype="datetime64[us]")),
        pa.array(["a", "b"]),
        pa.array([], type=pa.int64()),
        pa.array(np.arange(9, dtype=np.int64)).slice(3, 4),
        pa.array(np.arange(40) % 2 == 0).slice(5, 20),
        pa.chunked_array([pa.array([1, 2]), pa.array([3])]),
    ]


@pytest.mark.parametrize("arr", _arrays(), ids=lambda a: f"{a.type}-{len(a)}")
def test_to_numpy_matches_pyarrow(arr: Any) -> None:
    got = arrowkit.to_numpy(arr)
    want = arr.to_numpy(zero_copy_only=False)
    assert got.dtype == want.dtype
    np.testing.assert_array_equal(got, want)


def test_scalar_and_fill_null_match_pyarrow() -> None:
    for value, kind in [(None, pa.int64()), (0, pa.float64()), (3, None), (2.5, None)]:
        assert arrowkit.scalar(value, kind).equals(pa.scalar(value, type=kind))
    assert arrowkit.scalar("x").equals(pa.scalar("x"))
    assert arrowkit.scalar(True).equals(pa.scalar(True))
    assert arrowkit.scalar(0, pa.date32()).equals(pa.scalar(0, type=pa.date32()))
    for arr, fill in [
        (pa.array([1, None, 3]), 0),
        (pa.array([1.5, None]), float("nan")),
        (pa.array([True, None]), False),
        (pa.array(np.array(["2024-01-01", "NaT"], dtype="datetime64[us]")), 0),
    ]:
        assert same(arrowkit.fill_null(arr, fill), pc.fill_null(arr, pa.scalar(fill, arr.type)))


def test_no_pandas_is_imported() -> None:
    code = textwrap.dedent(
        """
        import sys
        import numpy as np
        import pyarrow as pa
        import pyarrow.compute as pc
        from shape.generation import arrowkit
        a = arrowkit.array(np.arange(5, dtype=np.int64))
        b = arrowkit.array(np.arange(5) > 1, mask=np.arange(5) == 0)
        c = arrowkit.array(["a", "b"])
        arrowkit.to_numpy(a)
        arrowkit.to_numpy(b.is_null())
        arrowkit.to_numpy(arrowkit.array(np.array([1.0, 2.0]), mask=np.array([True, False])))
        arrowkit.fill_null(a, 0)
        arrowkit.scalar(None, pa.int64())
        pc.if_else(arrowkit.array(np.arange(2) > 0), arrowkit.scalar(None, pa.string()), c)
        sys.exit(1 if "pandas" in sys.modules else 0)
        """
    )
    assert subprocess.run([sys.executable, "-c", code], check=False).returncode == 0
