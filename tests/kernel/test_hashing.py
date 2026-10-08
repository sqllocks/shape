"""P1-02: canonical hashing (T-13), Rust vs the Python reference, and the P7/S1 regressions."""

from __future__ import annotations

import datetime as dt
import decimal
import os
import struct
import subprocess
import sys
import zlib

import numpy as np
import pyarrow as pa
import pyarrow.compute
import pytest
import xxhash
from hypothesis import given, settings
from hypothesis import strategies as st

from shape.kernel import dispatch, hashing, reference
from shape.kernel.reference import hashing as ref_hashing
from shape.kernel.reference.xxh3 import xxh3_64_with_seed

N = 1_000_000


@pytest.fixture(scope="module")
def native():
    return dispatch._import_native()


def _make(name: str, n: int = N) -> pa.Array:
    """A 10^6-value array of one Arrow primitive type, with nulls and special values."""
    rng = np.random.default_rng(zlib.crc32(name.encode()))
    kind, _, unit = name.partition("_")
    i64 = rng.integers(-(2**63), 2**63 - 1, n, dtype=np.int64)
    if name.startswith(("int", "uint")) and name[-1].isdigit():
        dt_ = np.dtype(name)
        info = np.iinfo(dt_)
        arr = pa.array(rng.integers(info.min, info.max, n, dtype=dt_, endpoint=True))
    elif name.startswith("float"):
        f = rng.normal(size=n) * 10.0 ** rng.integers(-5, 25, n)
        f[::97] = np.nan
        f[1::101] = np.inf
        f[2::103] = -np.inf
        f[3::107] = -0.0
        f[4::109] = np.round(f[4::109])  # integral floats
        if name == "float16":
            f = rng.integers(-1000, 1000, n).astype(np.float64)
            f[::97] = np.nan
        arr = pa.array(f.astype(np.dtype(name)))
    elif name == "bool":
        arr = pa.array(rng.random(n) < 0.5)
    elif name in ("string", "large_string", "string_view"):
        strings = np.array([f"k{v}" for v in rng.integers(0, 10**7, n)], dtype=object)
        strings[::89] = "héllo wörld ✓"
        arr = pa.array(strings, type=getattr(pa, name)())
    elif name in ("binary", "large_binary"):
        nb = rng.integers(0, 256, (n, 5), dtype=np.uint8)
        arr = pa.array([bytes(r) for r in nb], type=getattr(pa, name)())
    elif name == "date32":
        arr = pa.array(rng.integers(-30000, 60000, n).astype(np.int32), type=pa.date32())
    elif name == "date64":
        ms = rng.integers(-(10**12), 10**12, n) // 86_400_000 * 86_400_000
        arr = pa.array(ms, type=pa.date64())
    elif kind == "time32":
        hi = 86_400 if unit == "s" else 86_400_000
        arr = pa.array(rng.integers(0, hi, n).astype(np.int32), type=pa.time32(unit))
    elif kind == "time64":
        per = {"us": 10**6, "ns": 10**9}[unit]
        arr = pa.array(rng.integers(0, 86_400 * per, n), type=pa.time64(unit))
    elif name == "decimal128":
        vals = [decimal.Decimal(int(v)).scaleb(-3) for v in rng.integers(-(10**9), 10**9, n)]
        arr = pa.array(vals, type=pa.decimal128(20, 3))
    elif kind == "timestamp":
        tz = "UTC" if name.endswith("_tz") else None
        arr = pa.array(i64, type=pa.timestamp(unit.split("_")[0], tz=tz))
    elif kind == "duration":
        arr = pa.array(i64, type=pa.duration(unit))
    else:  # pragma: no cover
        raise AssertionError(name)
    return arr


CASES = (
    ["int8", "int16", "int32", "int64", "uint8", "uint16", "uint32", "uint64"]
    + ["float16", "float32", "float64", "bool", "string", "large_string", "string_view"]
    + ["binary", "large_binary", "date32", "date64", "time32_s", "time32_ms", "time64_us"]
    + ["time64_ns", "decimal128"]
    + [f"{k}_{u}" for k in ("timestamp", "duration") for u in ("s", "ms", "us", "ns")]
    + [f"timestamp_{u}_tz" for u in ("s", "ms", "us", "ns")]
)


@pytest.mark.heavy
@pytest.mark.parametrize("name", CASES)
def test_rust_equals_reference_on_a_million_values(native, name):
    arr = _make(name)
    assert len(arr) == N
    # sprinkle nulls in every type
    rng = np.random.default_rng(1)
    null_at = rng.random(N) < 0.03
    view = pa.types.is_string_view(arr.type)  # if_else has no string_view kernel
    base = arr.cast(pa.string()) if view else arr
    mask = pa.array(null_at)
    if pa.types.is_float16(base.type):  # pyarrow 19 has no halffloat if_else (#333)
        idx = pa.compute.if_else(mask, pa.scalar(N, pa.int64()), pa.array(np.arange(N)))
        base = pa.concat_arrays([base, pa.nulls(1, base.type)]).take(idx)
    else:
        base = pa.compute.if_else(mask, pa.scalar(None, base.type), base)
    arr = base.cast(pa.string_view()) if view else base
    assert arr.null_count > 0
    for seed in (0, 0x5EED):
        got = pa.array(native.hash_array(arr, seed))
        want = reference.hash_array(arr, seed)
        assert got.type == pa.uint64() and len(got) == N
        assert got.equals(want), name
    # null inputs are null outputs
    assert (
        got.is_null()
        .to_numpy(zero_copy_only=False)[arr.is_null().to_numpy(zero_copy_only=False)]
        .all()
    )


def test_nulls_in_the_input_are_null_in_the_output(native):
    for typ, vals in [
        (pa.int64(), [1, None, 3]),
        (pa.string(), ["a", None, "c"]),
        (pa.float64(), [1.0, None, 3.0]),
        (pa.timestamp("us"), [1, None, 3]),
        (pa.bool_(), [True, None, False]),
    ]:
        arr = pa.array(vals, type=typ)
        for h in (pa.array(native.hash_array(arr)), reference.hash_array(arr)):
            assert h.to_pylist()[1] is None
            assert h.null_count == 1


def test_nan_is_excluded_but_infinities_are_not(native):
    arr = pa.array([float("nan"), float("inf"), float("-inf"), 1.5])
    for h in (pa.array(native.hash_array(arr)), reference.hash_array(arr)):
        v = h.to_pylist()
        assert v[0] is None and None not in v[1:]
        assert v[1] != v[2]


def test_one_and_one_point_zero_hash_equal(native):
    ints = [pa.array([1], type=t) for t in (pa.int8(), pa.int64(), pa.uint16(), pa.uint64())]
    floats = [pa.array([1.0], type=t) for t in (pa.float32(), pa.float64())]
    floats.append(pa.array(np.array([1.0], np.float16)))  # portable float16 construction (#333)
    dec = [pa.array([decimal.Decimal("1.000")], type=pa.decimal128(10, 3))]
    hashes = {pa.array(native.hash_array(a, 9)).to_pylist()[0] for a in ints + floats + dec}
    assert len(hashes) == 1
    assert hashing.hash_value(1, 9) == hashing.hash_value(1.0, 9) == hashes.pop()
    assert hashing.hash_value(-0.0) == hashing.hash_value(0)
    assert hashing.hash_value(True) != hashing.hash_value(1)
    assert hashing.hash_value(2**63) == hashing.hash_value(float(2**63))
    assert hashing.hash_value(1.5) != hashing.hash_value(1)


def test_string_flavours_and_time_units_agree(native):
    h = {
        pa.array(native.hash_array(pa.array(["héllo"], type=t))).to_pylist()[0]
        for t in (pa.string(), pa.large_string(), pa.string_view())
    }
    assert len(h) == 1
    assert hashing.hash_value("héllo") in h
    us = 86_400 * 1_000_000
    same = [
        pa.array([86_400], type=pa.timestamp("s")),
        pa.array([86_400_000], type=pa.timestamp("ms")),
        pa.array([us], type=pa.timestamp("us")),
        pa.array([us * 1000], type=pa.timestamp("ns")),
        pa.array([1], type=pa.date32()),
        pa.array([86_400_000], type=pa.date64()),
    ]
    out = {pa.array(native.hash_array(a)).to_pylist()[0] for a in same}
    assert len(out) == 1
    assert hashing.hash_value(dt.datetime(1970, 1, 2)) in out
    assert hashing.hash_value(dt.date(1970, 1, 2)) in out


def test_hash_is_xxh3_of_the_canonical_bytes(native):
    seed = 42
    want = xxhash.xxh3_64_intdigest(b"\x01" + struct.pack("<q", 5), seed=seed)
    assert pa.array(native.hash_array(pa.array([5]), seed)).to_pylist() == [want]
    want = xxhash.xxh3_64_intdigest(b"\x04" + b"abc", seed=seed)
    assert pa.array(native.hash_array(pa.array(["abc"]), seed)).to_pylist() == [want]


@pytest.mark.parametrize(
    "n", [0, 1, 3, 4, 8, 9, 16, 17, 100, 128, 129, 200, 240, 241, 1023, 1024, 5000]
)
def test_pure_python_xxh3_matches_the_library(n):
    data = bytes((i * 31 + 7) % 256 for i in range(n))
    for seed in (0, 1, 2**64 - 1, 0xDEADBEEF):
        assert xxh3_64_with_seed(data, seed) == xxhash.xxh3_64_intdigest(data, seed=seed)


def test_hash_column_handles_chunked_arrays(native):
    chunked = pa.chunked_array([pa.array([1, 2, None]), pa.array([4])])
    out = hashing.hash_column(chunked, 3)
    assert out.type == pa.uint64() and len(out) == 4 and out.null_count == 1


@settings(max_examples=200, deadline=None)
@given(
    st.lists(
        st.one_of(
            st.integers(-(2**63), 2**64 - 1),
            st.floats(allow_nan=True, allow_infinity=True),
            st.text(max_size=20),
            st.binary(max_size=20),
            st.booleans(),
            st.none(),
        ),
        max_size=20,
    ),
    st.integers(0, 2**64 - 1),
)
def test_scalar_hash_matches_column_hash(values, seed):
    native = dispatch._import_native()
    for v in values:
        typ = None
        if isinstance(v, int) and not isinstance(v, bool):
            typ = pa.int64() if v <= 2**63 - 1 else pa.uint64()
        arr = pa.array([v], type=typ)
        assert pa.array(native.hash_array(arr, seed)).to_pylist() == [
            ref_hashing.hash_value(v, seed)
        ]


@settings(max_examples=100, deadline=None)
@given(st.integers(-(2**53), 2**53))
def test_integral_floats_hash_like_their_integers(i):
    assert hashing.hash_value(float(i)) == hashing.hash_value(i)


def test_unsupported_types_raise_value_error(native):
    with pytest.raises(ValueError, match="unsupported"):
        native.hash_array(pa.array([[1]]))
    with pytest.raises(ValueError, match="unsupported"):
        reference.hash_array(pa.array([[1]]))


_HASHSEED_PROBE = """
import pyarrow as pa
from shape.kernel import hashing
from shape.kernel.values import DistinctCounter
from shape.streaming.platinum import HashedDependencyEvidence, hashed_dependency_batch
import numpy as np
print(hashing.hash_column(pa.array(["a", "bb"]), 7).to_pylist(),
      hashing.hash_column(pa.array([1, 2]), 7).to_pylist(),
      hashing.hash_value("x"), hashing.hash_value(2.5))
hll = DistinctCounter()
for v in ["a", 1, 2.5, "zz", 1.0]:
    hll.update(v)
regs = hll._h.registers()
print(sum(regs), [i for i, r in enumerate(regs) if r])
ev = HashedDependencyEvidence(64)
ev.update("a", 1); ev.update("b", 2.5)
print(sorted(ev.table.items()))
print(sorted(hashed_dependency_batch(np.arange(50), np.arange(50) % 7).table.items())[:5])
"""


def test_hashes_are_identical_under_any_pythonhashseed():
    """S1: no Python hash() anywhere; results do not depend on PYTHONHASHSEED."""
    outputs = []
    for seed in ("0", "1", "random", "4242"):
        env = dict(os.environ, PYTHONHASHSEED=seed)
        r = subprocess.run(
            [sys.executable, "-c", _HASHSEED_PROBE], capture_output=True, text=True, env=env
        )
        assert r.returncode == 0, r.stderr
        outputs.append(r.stdout)
    assert len(set(outputs)) == 1, outputs


def test_p7_sketches_ignore_nan_and_treat_1_and_1_point_0_alike():
    from shape.capture import capture_columns
    from shape.kernel.values import DistinctCounter, TopValues

    a, b = DistinctCounter(), DistinctCounter()
    a.update(1)
    b.update(1.0)
    assert a._h.registers() == b._h.registers() and any(a._h.registers())
    n = DistinctCounter()
    n.update(float("nan"))
    n.update(None)
    assert not any(n._h.registers())
    ss = TopValues()
    for _ in range(3):
        ss.update(float("nan"))
    assert ss.top() == []
    col = capture_columns({"x": [1, 1.0, float("nan"), float("nan"), 2]})["columns"]["x"]
    assert col["nan_count"] == 2
    assert col["topk"][0][:2] == [1, 2]  # 1 and 1.0 are one value; NaN is not a key
    assert all(item[0] == item[0] for item in col["topk"])


def test_s1_platinum_bins_use_the_stable_hash():
    from shape.streaming.platinum import HashedDependencyEvidence

    ev = HashedDependencyEvidence(64)
    expected = hashing.hash_value("abc") % 64
    assert ev._bin("abc") == expected
    assert ev._bin(float("nan")) is None
    ev.update("a", float("nan"))
    assert ev.n == 0


# ---- hash_value agrees with hash_column for numpy, pandas and Arrow scalars (#538) -------------


def _null_likes():
    import pandas as pd

    return [
        pd.NaT,
        pd.NA,
        np.datetime64("NaT"),
        np.datetime64("NaT", "ns"),
        np.timedelta64("NaT"),
        pa.scalar(None, pa.int64()),
        pa.scalar(None, pa.timestamp("ns")),
    ]


def test_null_likes_hash_to_none():
    # Regression #538: pd.NA and NaT got hashes; pd.NaT raised struct.error.
    from shape.kernel.hashing import hash_value
    from shape.kernel.values import DistinctCounter, TopValues

    for value in _null_likes():
        assert hash_value(value) is None, repr(value)
    top, distinct = TopValues(), DistinctCounter()
    for value in [1, *_null_likes(), 1]:
        top.update(value)
        distinct.update(value)
    assert top.top() == [[1, 2, 0]] and round(distinct.estimate()) == 1


@pytest.mark.parametrize(
    ("scalar", "array"),
    [
        (np.datetime64(1_000_000_999, "ns"), pa.array([1_000_000_999], pa.timestamp("ns"))),
        (np.datetime64(-1_999, "ns"), pa.array([-1_999], pa.timestamp("ns"))),
        (np.datetime64(3, "s"), pa.array([3], pa.timestamp("s"))),
        (np.datetime64("2024-02-29"), pa.array([19782], pa.date32())),
        (np.timedelta64(5_500, "ns"), pa.array([5_500], pa.duration("ns"))),
        (np.timedelta64(7, "ms"), pa.array([7], pa.duration("ms"))),
        (pa.scalar(1), pa.array([1])),
        (pa.scalar(1_500, pa.timestamp("ns")), pa.array([1_500], pa.timestamp("ns"))),
        (pa.scalar(2, pa.duration("us")), pa.array([2], pa.duration("us"))),
        (pa.scalar("x"), pa.array(["x"])),
    ],
)
def test_hash_value_of_numpy_and_arrow_scalars_equals_hash_column(scalar, array):
    # Regression #538: datetime64[ns] hashed as a plain integer, Arrow scalars as "other".
    from shape.kernel.hashing import hash_column, hash_value

    assert hash_value(scalar) == hash_column(array)[0].as_py()


@pytest.mark.parametrize("kernel", ["rust", "python"])
@pytest.mark.parametrize(
    ("values", "typ"),
    [
        (["1E+3", "-2E+3", None], pa.decimal128(5, -3)),
        (["12E+2", "0"], pa.decimal128(4, -2)),
    ],
)
def test_negative_scale_decimals_hash_like_their_integers(native, kernel, values, typ):
    # Regression #540: the twin computed 10 ** (negative int), a float, and struct.pack failed.
    from decimal import Decimal

    mod = native if kernel == "rust" else reference
    array = pa.array([None if v is None else Decimal(v) for v in values], typ)
    got = pa.array(mod.hash_array(array, 0)).to_pylist()
    want = [None if v is None else reference.hash_array(pa.array([int(Decimal(v))]), 0)[0].as_py()
            for v in values]  # fmt: skip
    assert got == want
