"""Arrow array construction that never imports pandas.

``pyarrow.array`` probes for pandas the first time it is called, and importing pandas costs about
0.16 s of every process that generates data. :func:`array` builds the same arrays for the inputs
the engine and its strategies pass (numpy arrays of a fixed-width dtype, optionally with a null
mask, Arrow arrays, short lists of ``str``) straight from buffers, without copying, and hands every
other input to ``pyarrow.array`` unchanged, so the result is the same either way.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np
import numpy.typing as npt
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]

__all__ = ["array", "fill_null", "raw_numpy", "scalar", "to_numpy"]

_FIXED_KINDS = frozenset("biufM")
# Arrow types of the dtypes that nearly every array the engine builds has (a plain int64 or float64
# array with no mask and no type asked for goes straight to its buffer in :func:`array`).
_PLAIN = {np.dtype(np.int64): pa.int64(), np.dtype(np.float64): pa.float64()}


def _bitmap(flags: npt.NDArray[np.bool_]) -> Any:
    return pa.py_buffer(np.packbits(flags, bitorder="little"))


def _from_numpy(
    values: npt.NDArray[Any],
    type: pa.DataType | None,
    mask: npt.NDArray[np.bool_] | None,
    from_pandas: bool,
) -> pa.Array | None:
    """The array for ``values`` or ``None`` when ``pyarrow.array`` has to decide."""
    if values.ndim != 1 or values.dtype.kind not in _FIXED_KINDS or not values.dtype.isnative:
        return None
    if values.dtype.kind == "M" and np.datetime_data(values.dtype)[0] in ("Y", "M", "W", "h", "m"):
        return None  # a unit Arrow does not have: let pyarrow convert it
    if values.dtype.kind == "M":  # NaT is null, as in pyarrow
        nat = np.isnat(values)
        mask = nat if mask is None else (np.asarray(mask, dtype=np.bool_) | nat)
    elif from_pandas and values.dtype.kind == "f":
        nan = np.isnan(values)
        mask = nan if mask is None else (np.asarray(mask, dtype=np.bool_) | nan)
    natural = pa.from_numpy_dtype(values.dtype)
    if type is not None and type != natural:
        return None
    n = len(values)
    if mask is not None:
        mask = np.asarray(mask, dtype=np.bool_)
        if mask.shape != values.shape:
            return None
        nulls = int(mask.sum())
        validity = _bitmap(~mask) if nulls else None
    else:
        nulls, validity = 0, None
    if values.dtype.kind == "b":
        data = _bitmap(values)
    elif pa.types.is_date32(natural):
        data = pa.py_buffer(values.view(np.int64).astype(np.int32))
    else:
        data = pa.py_buffer(np.ascontiguousarray(values))
    out: pa.Array = pa.Array.from_buffers(natural, n, [validity, data], null_count=nulls)
    return out


def _from_strings(items: list[Any], type: pa.DataType | None) -> pa.Array | None:
    if (type is not None and type != pa.string()) or not all(isinstance(s, str) for s in items):
        return None
    encoded = [s.encode() for s in items]
    offsets = np.zeros(len(encoded) + 1, dtype=np.int32)
    np.cumsum([len(b) for b in encoded], out=offsets[1:])
    out: pa.Array = pa.Array.from_buffers(
        pa.string(), len(encoded), [None, pa.py_buffer(offsets), pa.py_buffer(b"".join(encoded))]
    )
    return out


def _from_sequence(
    items: Sequence[Any], type: pa.DataType | None, from_pandas: bool
) -> pa.Array | None:
    """A non-empty list or tuple of only ``str``, only ``float``, only ``int`` (within int64) or
    only ``bool``; anything else (``None``, a mix, other types) is left to pyarrow."""
    kinds = {x.__class__ for x in items}
    if kinds == {str}:
        return _from_strings(list(items), type)
    if kinds == {float}:
        return _from_numpy(np.asarray(items, dtype=np.float64), type, None, from_pandas)
    if kinds == {bool}:
        return _from_numpy(np.asarray(items, dtype=np.bool_), type, None, from_pandas)
    if kinds == {int} and all(-(2**63) <= x < 2**63 for x in items):
        return _from_numpy(np.asarray(items, dtype=np.int64), type, None, from_pandas)
    return None


def array(
    obj: Any,
    type: pa.DataType | None = None,
    mask: Any = None,
    from_pandas: bool = False,
) -> pa.Array:
    """``pyarrow.array(obj, type=type, mask=mask, from_pandas=from_pandas)``, without pandas for
    numpy arrays of a fixed-width dtype, Arrow arrays and lists of ``str``."""
    fast: pa.Array | None = None
    if obj.__class__ is np.ndarray and mask is None and type is None and not from_pandas:
        plain = _PLAIN.get(obj.dtype)
        if plain is not None and obj.ndim == 1 and obj.flags.c_contiguous:
            out: pa.Array = pa.Array.from_buffers(plain, len(obj), [None, pa.py_buffer(obj)])
            return out
    if isinstance(obj, pa.Array):
        if mask is None and (type is None or type == obj.type):
            return obj
    elif isinstance(obj, np.ndarray):
        fast = _from_numpy(obj, type, None if mask is None else np.asarray(mask), from_pandas)
    elif isinstance(obj, list | tuple) and mask is None and obj:
        fast = _from_sequence(obj, type, from_pandas)
    if fast is not None:
        return fast
    return pa.array(obj, type=type, mask=mask, from_pandas=from_pandas)


def _numpy_dtype(t: pa.DataType) -> np.dtype[Any] | None:
    if pa.types.is_integer(t):
        return np.dtype(f"{'i' if pa.types.is_signed_integer(t) else 'u'}{t.bit_width // 8}")
    if pa.types.is_floating(t):
        return np.dtype(f"f{t.bit_width // 8}")
    if pa.types.is_timestamp(t) and t.tz is None:
        return np.dtype(f"datetime64[{t.unit}]")
    if pa.types.is_date32(t):
        return np.dtype("int32")
    return None


def to_numpy(value: Any) -> npt.NDArray[Any]:
    """``value.to_numpy(zero_copy_only=False)`` without pandas for null-free or null-holding
    integer, floating, boolean, ``date32`` and zone-less timestamp arrays (other types go through
    pyarrow). Nulls become NaN (integers become ``float64``) or NaT, as in pyarrow. Values of a
    null-free fixed-width array are a read-only view of the Arrow buffer, as in pyarrow."""
    arr = value.combine_chunks() if isinstance(value, pa.ChunkedArray) else value
    t = arr.type
    n = len(arr)
    if n == 0 and (pa.types.is_boolean(t) or _numpy_dtype(t) is not None):
        empty = np.empty(0, dtype=np.bool_ if pa.types.is_boolean(t) else _numpy_dtype(t))
        return empty.astype("datetime64[D]") if pa.types.is_date32(t) else empty
    if pa.types.is_boolean(t) and arr.null_count == 0:
        raw = np.frombuffer(arr.buffers()[1], dtype=np.uint8)
        bits = np.unpackbits(raw, bitorder="little")[arr.offset : arr.offset + n]
        return bits.astype(np.bool_)
    dtype = _numpy_dtype(t)
    if dtype is None:
        out: npt.NDArray[Any] = arr.to_numpy(zero_copy_only=False)
        return out
    data = arr.buffers()[1]
    values = np.frombuffer(data, dtype=dtype, count=n, offset=arr.offset * dtype.itemsize)
    if pa.types.is_date32(t):
        values = values.astype("datetime64[D]")
    if arr.null_count:
        valid = to_numpy(arr.is_valid())
        values = values.astype(np.float64) if values.dtype.kind in "iu" else values.copy()
        values[~valid] = np.datetime64("NaT") if values.dtype.kind == "M" else np.nan
    return values


def scalar(value: Any, type: pa.DataType | None = None) -> pa.Scalar:
    """``pyarrow.scalar(value, type=type)`` without pandas for ``None``, ``bool``, ``int``,
    ``float`` and ``str`` values."""
    if value is None and type is not None:
        return pa.nulls(1, type)[0]
    one: pa.Array | None = None
    if isinstance(value, bool):
        one = array(np.array([value], dtype=np.bool_))
    elif isinstance(value, int) and -(2**63) <= value < 2**63:
        one = array(np.array([value], dtype=np.int64))
    elif isinstance(value, float):
        one = array(np.array([value], dtype=np.float64))
    elif isinstance(value, str):
        one = array([value])
    if one is None:
        return pa.scalar(value, type=type)
    if type is not None and type != one.type:
        try:
            one = one.cast(type)
        except (pa.ArrowNotImplementedError, pa.ArrowInvalid):
            return pa.scalar(value, type=type)
    return one[0]


def fill_null(values: Any, fill: Any) -> Any:
    """``pyarrow.compute.fill_null(values, fill)`` for a Python ``fill`` value, without pandas."""
    return pc.fill_null(values, scalar(fill, values.type))


def raw_numpy(values: pa.Array) -> tuple[npt.NDArray[Any], npt.NDArray[np.bool_] | None]:
    """The values of an integer or floating Arrow array as numpy (a read-only view of the Arrow
    buffer; what a null row holds is undefined) and the validity mask (``None`` without nulls).
    Unlike :func:`to_numpy` an integer array keeps its dtype when it has nulls."""
    dtype = _numpy_dtype(values.type)
    if dtype is None or dtype.kind not in "iuf":
        raise TypeError(f"raw_numpy takes an integer or floating array, not {values.type}")
    n = len(values)
    if n == 0:
        return np.empty(0, dtype=dtype), None
    data = np.frombuffer(
        values.buffers()[1], dtype=dtype, count=n, offset=values.offset * dtype.itemsize
    )
    return data, (to_numpy(values.is_valid()) if values.null_count else None)
