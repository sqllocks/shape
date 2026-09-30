"""Canonical value hashing, pure-Python reference twin of ``rust/shape-kernel/src/hashing.rs``.

T-13: seeded XXH3-64 over a canonical byte form, never Python ``hash()``:

* integers and integral floats hash equal (``1`` == ``1.0``); ``-0.0`` is ``0``;
* NaN and null are excluded: their slot in the output is null;
* strings hash as UTF-8 bytes; timestamps as int64 microseconds.

Canonical bytes are one tag byte plus a payload (see the Rust module for the table). The hash
comes from the ``xxhash`` package when it is installed and from the pure-Python XXH3 in
``xxh3.py`` otherwise; both give the same value.
"""

from __future__ import annotations

import datetime as _dt
import decimal as _dc
import math
import struct
from collections.abc import Callable
from typing import Any

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]

from .xxh3 import xxh3_64_with_seed as _py_xxh3

try:  # the fast path; identical results to the fallback
    import xxhash as _xxhash

    _hash_bytes: Callable[[bytes, int], int] = lambda b, s: int(  # noqa: E731
        _xxhash.xxh3_64_intdigest(b, seed=s)
    )
    HAS_XXHASH = True
except ImportError:  # pragma: no cover - exercised in the pure wheel
    _hash_bytes = _py_xxh3
    HAS_XXHASH = False

TAG_INT, TAG_UINT, TAG_FLOAT, TAG_STR, TAG_BIN = 1, 2, 3, 4, 5
TAG_BOOL, TAG_TS, TAG_DUR, TAG_TIME, TAG_DEC, TAG_BIGINT = 6, 7, 8, 9, 10, 11
TAG_OTHER = 0x0D

_I64_MIN, _I64_MAX, _U64_MAX = -(2**63), 2**63 - 1, 2**64 - 1
_UNIT_MUL = {"s": 1_000_000, "ms": 1_000, "us": 1}


def _wrap_i64(v: int) -> int:
    v &= 0xFFFFFFFFFFFFFFFF
    return v - (1 << 64) if v >= (1 << 63) else v


def _i64(tag: int, v: int) -> bytes:
    return bytes([tag]) + struct.pack("<q", v)


def _int_bytes(v: int) -> bytes:
    if _I64_MIN <= v <= _I64_MAX:
        return _i64(TAG_INT, v)
    if 0 < v <= _U64_MAX:
        return bytes([TAG_UINT]) + struct.pack("<Q", v)
    return bytes([TAG_BIGINT]) + (v & ((1 << 128) - 1)).to_bytes(16, "little")


def float_bytes(x: float) -> bytes | None:
    """Canonical bytes of a float, or None for NaN."""
    if math.isnan(x):
        return None
    if math.isfinite(x) and x == math.trunc(x):
        if -(2.0**63) <= x < 2.0**63:
            return _i64(TAG_INT, int(x))
        if 2.0**63 <= x < 2.0**64:
            return bytes([TAG_UINT]) + struct.pack("<Q", int(x))
    return bytes([TAG_FLOAT]) + struct.pack("<d", x)


def decimal_bytes(unscaled: int, scale: int) -> bytes:
    """Canonical bytes of ``unscaled * 10**-scale``."""
    if scale < 0:
        cur = unscaled * 10 ** (-scale)
        if -(2**127) <= cur < 2**127:
            unscaled, scale = cur, 0
    else:
        while scale > 0 and unscaled % 10 == 0:
            unscaled //= 10
            scale -= 1
    if scale == 0:
        return _int_bytes(unscaled)
    return bytes([TAG_DEC, scale & 0xFF]) + (unscaled & ((1 << 128) - 1)).to_bytes(16, "little")


def _time_us(v: int, unit: str) -> int:
    if unit == "ns":
        return v // 1000  # floor, like div_euclid
    return _wrap_i64(v * _UNIT_MUL[unit])


def canonical_bytes(value: Any) -> bytes | None:
    """Canonical bytes of a Python scalar; ``None`` for null and NaN (excluded from hashing)."""
    if value is None:
        return None
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, bool):
        return bytes([TAG_BOOL, int(value)])
    if isinstance(value, int):
        return _int_bytes(value)
    if isinstance(value, float):
        return float_bytes(value)
    if isinstance(value, str):
        return bytes([TAG_STR]) + value.encode("utf-8", "surrogatepass")
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes([TAG_BIN]) + bytes(value)
    if isinstance(value, _dt.datetime):
        if value.tzinfo is not None:
            value = value.astimezone(_dt.UTC).replace(tzinfo=None)
        delta = value - _dt.datetime(1970, 1, 1)
        return _i64(TAG_TS, (delta.days * 86_400 + delta.seconds) * 1_000_000 + delta.microseconds)
    if isinstance(value, _dt.date):
        return _i64(TAG_TS, _wrap_i64((value - _dt.date(1970, 1, 1)).days * 86_400_000_000))
    if isinstance(value, _dt.time):
        return _i64(
            TAG_TIME,
            ((value.hour * 60 + value.minute) * 60 + value.second) * 1_000_000 + value.microsecond,
        )
    if isinstance(value, _dt.timedelta):
        return _i64(TAG_DUR, (value.days * 86_400 + value.seconds) * 1_000_000 + value.microseconds)
    if isinstance(value, _dc.Decimal):
        if not value.is_finite():
            return None if value.is_nan() else bytes([TAG_OTHER]) + str(value).encode("ascii")
        sign, digits, exp = value.as_tuple()
        unscaled = int("".join(map(str, digits)) or "0") * (-1 if sign else 1)
        assert isinstance(exp, int)
        return decimal_bytes(unscaled, -exp)
    return bytes([TAG_OTHER]) + (type(value).__name__ + ":" + repr(value)).encode(
        "utf-8", "surrogatepass"
    )


def hash_value(value: Any, seed: int = 0) -> int | None:
    """Canonical hash of a Python scalar, or None for null/NaN."""
    b = canonical_bytes(value)
    return None if b is None else _hash_bytes(b, seed)


def _float_hashes(values: np.ndarray, seed: int) -> list[int | None]:
    return [None if (b := float_bytes(float(x))) is None else _hash_bytes(b, seed) for x in values]


def _is_view(t: Any, kind: str) -> bool:
    """``string_view`` / ``binary_view`` exist from pyarrow 16; older versions never see them."""
    check = getattr(pa.types, f"is_{kind}_view", None)
    return bool(check and check(t))


def hash_array(array: Any, seed: int = 0) -> Any:
    """Canonical hash of every element of an Arrow array; null and NaN slots are null in the
    returned uint64 array. Twin of ``shape._kernel.hash_array``."""
    if not isinstance(array, pa.Array):
        array = pa.array(array)
    t = array.type
    n = len(array)
    if pa.types.is_null(t):
        return pa.nulls(n, type=pa.uint64())
    valid = None if array.null_count == 0 else array.is_valid().to_numpy(zero_copy_only=False)
    out: list[int | None]
    if pa.types.is_integer(t):
        vals = array.fill_null(0).to_numpy(zero_copy_only=False)
        out = [_hash_bytes(_int_bytes(int(v)), seed) for v in vals]
    elif pa.types.is_floating(t):
        vals = array.cast(pa.float64()).fill_null(0.0).to_numpy(zero_copy_only=False)
        out = _float_hashes(vals, seed)
    elif pa.types.is_boolean(t):
        vals = array.fill_null(False).to_numpy(zero_copy_only=False)
        out = [_hash_bytes(bytes([TAG_BOOL, int(bool(v))]), seed) for v in vals]
    elif pa.types.is_string(t) or pa.types.is_large_string(t) or _is_view(t, "string"):
        out = [
            _hash_bytes(bytes([TAG_STR]) + s.encode("utf-8", "surrogatepass"), seed)
            for s in ("" if v is None else v for v in array.to_pylist())
        ]
    elif pa.types.is_binary(t) or pa.types.is_large_binary(t) or _is_view(t, "binary"):
        out = [_hash_bytes(bytes([TAG_BIN]) + b, seed) for b in array.fill_null(b"").to_pylist()]
    elif pa.types.is_date32(t):
        vals = array.cast(pa.int32()).fill_null(0).to_numpy(zero_copy_only=False)
        out = [_hash_bytes(_i64(TAG_TS, _wrap_i64(int(v) * 86_400_000_000)), seed) for v in vals]
    elif pa.types.is_date64(t):
        vals = array.cast(pa.int64()).fill_null(0).to_numpy(zero_copy_only=False)
        out = [_hash_bytes(_i64(TAG_TS, _wrap_i64(int(v) * 1000)), seed) for v in vals]
    elif pa.types.is_timestamp(t) or pa.types.is_duration(t):
        tag = TAG_TS if pa.types.is_timestamp(t) else TAG_DUR
        vals = array.cast(pa.int64()).fill_null(0).to_numpy(zero_copy_only=False)
        out = [_hash_bytes(_i64(tag, _time_us(int(v), t.unit)), seed) for v in vals]
    elif pa.types.is_time(t):
        unit = t.unit
        vals = array.cast(pa.int64() if t.bit_width == 64 else pa.int32()).fill_null(0)
        out = [
            _hash_bytes(_i64(TAG_TIME, _time_us(int(v), unit)), seed)
            for v in vals.to_numpy(zero_copy_only=False)
        ]
    elif pa.types.is_decimal128(t):
        scale = t.scale
        out = [
            _hash_bytes(decimal_bytes(_unscaled(d, scale), scale), seed)
            for d in array.fill_null(_dc.Decimal(0)).to_pylist()
        ]
    else:
        raise ValueError(f"hash_array: unsupported Arrow type {t}")
    if valid is not None:
        out = [h if ok else None for h, ok in zip(out, valid, strict=True)]
    result = pa.array(out, type=pa.uint64())
    assert len(result) == n
    return result


def _unscaled(d: _dc.Decimal, scale: int) -> int:
    """The unscaled integer of a decimal128 value at its column scale (exact, no rounding)."""
    sign, digits, exp = d.as_tuple()
    assert isinstance(exp, int)
    n: int = int("".join(map(str, digits)) or "0") * 10 ** int(exp + scale)
    return -n if sign else n
