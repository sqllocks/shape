"""Pure-Python twin of the generation kernel (P4-03, ``rust/shape-kernel/src/gen``).

Every function has the name, arguments and result of its ``shape._kernel`` counterpart. The
Philox words come from numpy's own ``Philox`` bit generator, which is the known-answer oracle
of the stream (T-16): the ``j``-th word of the stream keyed ``(k0, k1)`` is word ``j % 4`` of
``numpy.random.Philox(key=k0 | k1 << 64, counter=j // 4).random_raw(4)``.

Integer results (words, indices, alias tables, strings, timestamps) are equal to the native
kernel's bit for bit. Floating-point results that pass through ``log``, ``cos`` or ``exp``
(``philox_normal``, ``hour_weights_peaks``) agree to within a few ulp, because numpy and the
Rust standard library use different libm routines.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

import numpy as np
import numpy.typing as npt
import pyarrow as pa  # type: ignore[import-untyped]

from shape.generation.arrowkit import array as arrow_array
from shape.generation.arrowkit import to_numpy as arrow_numpy

_TWO_NEG_53 = 2.0**-53
_MAX_BELOW = 2**32 - 1
_US_PER_HOUR = 3_600_000_000
_US_PER_DAY = 86_400_000_000
# Same literals as the Rust constants (std::f64::consts::FRAC_2_SQRT_PI and SQRT_2).
_FRAC_2_SQRT_PI = 1.1283791670955126
_SQRT_2 = 1.4142135623730951


def _words(k0: int, k1: int, row_start: int, n_rows: int, per_row: int) -> npt.NDArray[np.uint64]:
    if per_row < 1:
        raise ValueError("per_row must be positive")
    if row_start < 0 or n_rows < 0:
        raise ValueError("row_start and n_rows must be non-negative")
    count = n_rows * per_row
    if count == 0:
        return np.empty(0, dtype=np.uint64)
    first = row_start * per_row
    block = first // 4
    total = (first + count + 3) // 4 * 4 - block * 4
    bits = np.random.Philox(key=int(k0) | (int(k1) << 64), counter=block)
    raw: npt.NDArray[np.uint64] = bits.random_raw(total)
    skip = first - block * 4
    return raw[skip : skip + count]


def _unit(w: npt.NDArray[np.uint64]) -> npt.NDArray[np.float64]:
    return (w >> np.uint64(11)).astype(np.float64) * _TWO_NEG_53


def _below(w: npt.NDArray[np.uint64], n: int) -> npt.NDArray[np.int64]:
    """``floor(w * n / 2**64)`` in 64-bit arithmetic (``n`` below 2**32)."""
    if not 0 < n <= _MAX_BELOW:
        raise ValueError(f"range {n} is out of bounds")
    nn = np.uint64(n)
    hi = w >> np.uint64(32)
    lo = w & np.uint64(0xFFFFFFFF)
    return ((hi * nn + ((lo * nn) >> np.uint64(32))) >> np.uint64(32)).astype(np.int64)


def _slot(per_row: int, slot: int, width: int) -> None:
    if per_row < 1 or slot < 0 or slot + width > per_row:
        raise ValueError(f"slot {slot} (+{width} words) does not fit in {per_row} words per row")


def philox_words(k0: int, k1: int, row_start: int, n_rows: int, per_row: int = 1) -> pa.Array:
    return arrow_array(_words(k0, k1, row_start, n_rows, per_row), type=pa.uint64())


def philox_uniform(
    k0: int, k1: int, row_start: int, n_rows: int, per_row: int = 1, slot: int = 0
) -> pa.Array:
    _slot(per_row, slot, 1)
    w = _words(k0, k1, row_start, n_rows, per_row).reshape(n_rows, per_row)
    return arrow_array(_unit(w[:, slot]))


def philox_normal(
    k0: int, k1: int, row_start: int, n_rows: int, per_row: int = 2, slot: int = 0
) -> pa.Array:
    _slot(per_row, slot, 2)
    w = _words(k0, k1, row_start, n_rows, per_row).reshape(n_rows, per_row)
    u1 = 1.0 - _unit(w[:, slot])
    u2 = _unit(w[:, slot + 1])
    with np.errstate(all="ignore"):
        z = np.sqrt(-2.0 * np.log(u1)) * np.cos(2.0 * math.pi * u2)
    return arrow_array(z)


def _f64(a: Any, what: str) -> npt.NDArray[np.float64]:
    arr = arrow_array(a) if not isinstance(a, pa.Array) else a
    if not pa.types.is_float64(arr.type):
        raise ValueError(f"{what} must be a float64 array")
    if arr.null_count:
        raise ValueError(f"{what} must not contain nulls")
    return np.asarray(arrow_numpy(arr), dtype=np.float64)


def _build_alias(weights: Sequence[float]) -> tuple[list[float], list[int]]:
    """Vose's method with exactly the operations of ``alias.rs`` (sequential sum, same order)."""
    n = len(weights)
    if n == 0:
        raise ValueError("alias_build needs at least one weight")
    total = 0.0
    for w in weights:
        if not math.isfinite(w) or w < 0.0:
            raise ValueError("alias weights must be finite and non-negative")
        total += w
    if total <= 0.0 or not math.isfinite(total):
        raise ValueError("alias weights must have a positive finite sum")
    nf = float(n)
    scaled = [w * nf / total for w in weights]
    prob = [0.0] * n
    alias = list(range(n))
    small: list[int] = []
    large: list[int] = []
    for i, s in enumerate(scaled):
        (small if s < 1.0 else large).append(i)
    while small and large:
        s = small.pop()
        big = large.pop()
        prob[s] = scaled[s]
        alias[s] = big
        scaled[big] = (scaled[big] + scaled[s]) - 1.0
        (small if scaled[big] < 1.0 else large).append(big)
    for big in large:
        prob[big] = 1.0
    for s in small:
        prob[s] = 1.0
    return prob, alias


def alias_build(weights: Any) -> tuple[pa.Array, pa.Array]:
    prob, alias = _build_alias([float(w) for w in _f64(weights, "weights")])
    return arrow_array(prob, type=pa.float64()), arrow_array(alias, type=pa.int64())


def _pick(
    prob: npt.NDArray[np.float64],
    alias: npt.NDArray[np.int64],
    w0: npt.NDArray[np.uint64],
    w1: npt.NDArray[np.uint64],
) -> npt.NDArray[np.int64]:
    i = _below(w0, len(prob))
    return np.where(_unit(w1) < prob[i], i, alias[i]).astype(np.int64)


def alias_sample(
    prob: Any,
    alias: Any,
    k0: int,
    k1: int,
    row_start: int,
    n_rows: int,
    per_row: int = 2,
    slot: int = 0,
) -> pa.Array:
    _slot(per_row, slot, 2)
    p = _f64(prob, "prob")
    a = np.asarray(arrow_numpy(arrow_array(alias)), dtype=np.int64)
    if len(p) == 0 or len(p) != len(a):
        raise ValueError("prob and alias must be non-empty and equally long")
    if ((a < 0) | (a >= len(p))).any():
        raise ValueError("alias entries must lie in 0..len(prob)")
    w = _words(k0, k1, row_start, n_rows, per_row).reshape(n_rows, per_row)
    return arrow_array(_pick(p, a, w[:, slot], w[:, slot + 1]), type=pa.int64())


def _index(words: npt.NDArray[np.uint64], size: int) -> npt.NDArray[np.int64]:
    """``min(floor(u * size), size - 1)`` for the uniforms of ``words``: the index of a uniform
    pool or key pick (``size`` is converted to a double first, as the native kernel does)."""
    u = _unit(words)
    return np.minimum((u * float(size)).astype(np.int64), size - 1)


def uniform_index(
    k0: int,
    k1: int,
    row_start: int,
    n_rows: int,
    size: int,
    per_row: int = 1,
    slot: int = 0,
) -> pa.Array:
    _slot(per_row, slot, 1)
    if size < 1:
        raise ValueError("size must be positive")
    w = _words(k0, k1, row_start, n_rows, per_row).reshape(n_rows, per_row)
    return arrow_array(_index(w[:, slot], size), type=pa.int64())


def pool_pick(pool: Any, k0: int, k1: int, row_start: int, n_rows: int) -> pa.Array:
    entries = _values(pool)
    if pa.types.is_integer(pool.type):
        raise ValueError("pool_pick needs a string pool")
    if not entries:
        raise ValueError("pool_pick needs a non-empty pool")
    index = _index(_words(k0, k1, row_start, n_rows, 1), len(entries))
    return arrow_array([entries[i] for i in index], type=pa.string())


def _alias_draw(
    prob: Any, alias: Any, size: int, k0: int, k1: int, row_start: int, n_rows: int,
    per_row: int, slot: int,
) -> npt.NDArray[np.int64]:  # fmt: skip
    _slot(per_row, slot, 2)
    p = _f64(prob, "prob")
    if len(p) == 0 or len(p) != size:
        raise ValueError("prob, alias and the values must have the same non-zero length")
    drawn = alias_sample(prob, alias, k0, k1, row_start, n_rows, per_row, slot)
    return np.asarray(arrow_numpy(drawn), dtype=np.int64)


def alias_pool(
    prob: Any,
    alias: Any,
    pool: Any,
    k0: int,
    k1: int,
    row_start: int,
    n_rows: int,
    per_row: int = 2,
    slot: int = 0,
) -> pa.Array:
    entries = _values(pool)
    if pa.types.is_integer(pool.type):
        raise ValueError("alias_pool needs a string pool")
    index = _alias_draw(prob, alias, len(entries), k0, k1, row_start, n_rows, per_row, slot)
    return arrow_array([entries[i] for i in index], type=pa.string())


def alias_values(
    prob: Any,
    alias: Any,
    values: Any,
    k0: int,
    k1: int,
    row_start: int,
    n_rows: int,
    per_row: int = 2,
    slot: int = 0,
) -> pa.Array:
    table = _f64(values, "values")
    index = _alias_draw(prob, alias, len(table), k0, k1, row_start, n_rows, per_row, slot)
    return arrow_array(table[index], type=pa.float64())


def _cum(cum: Any) -> npt.NDArray[np.float64]:
    values = _f64(cum, "cum")
    if len(values) == 0 or not np.isfinite(values).all() or (np.diff(values) < 0).any():
        raise ValueError("cum must be non-empty, finite and non-decreasing")
    return values


def zipf_guide(cum: Any) -> pa.Array:
    """The native kernel's guide table speeds up the search and never changes its answer; the
    reference searches the whole array, so its guide is the smallest valid one."""
    _cum(cum)
    return arrow_array(np.zeros(1024, dtype=np.int64), type=pa.int64())


def zipf_draw(cum: Any, guide: Any, k0: int, k1: int, row_start: int, n_rows: int) -> pa.Array:
    values = _cum(cum)
    table = np.asarray(arrow_numpy(arrow_array(guide)), dtype=np.int64)
    if (
        len(table) == 0
        or len(table) & (len(table) - 1)
        or ((table < 0) | (table > len(values))).any()
    ):
        raise ValueError("cum must be non-empty and guide a power-of-two table of its own")
    u = _unit(_words(k0, k1, row_start, n_rows, 1))
    found = np.searchsorted(values, u, side="right")
    return arrow_array(np.minimum(found, len(values) - 1).astype(np.int64), type=pa.int64())


# ---------------------------------------------------------------- strings


def _values(col: Any) -> list[Any]:
    arr = col if isinstance(col, pa.Array) else arrow_array(col)
    t = arr.type
    if not (pa.types.is_string(t) or pa.types.is_large_string(t) or pa.types.is_int64(t)):
        raise ValueError(f"expected string, large_string or int64, got {t}")
    return list(arr.to_pylist())


def _fmt(v: Any, width: int) -> str:
    if isinstance(v, str):
        return v
    return format(v, f"0{width}d") if width else str(v)


def pool_take(pool: Any, indices: Any) -> pa.Array:
    values = _values(pool)
    if pa.types.is_integer(pool.type):
        raise ValueError("pool_take needs a string pool")
    idx = arrow_array(indices)
    if not pa.types.is_int64(idx.type):
        raise ValueError("indices must be an int64 array")
    size = len(values)
    out: list[str | None] = []
    for v in idx.to_pylist():
        if v is None:
            out.append(None)
            continue
        if v < 0 or v >= size:
            raise ValueError(f"pool index {v} out of range 0..{size}")
        out.append(values[v])
    return arrow_array(out, type=pa.string())


def template_strings(
    literals: Sequence[str],
    slots: Sequence[tuple[int, int]],
    columns: Sequence[Any],
    n_rows: int,
) -> pa.Array:
    if len(literals) != len(slots) + 1:
        raise ValueError("template needs one more literal than slots")
    if any(c >= len(columns) for c, _ in slots):
        raise ValueError("template slot refers to a missing column")
    cols = [_values(c) for c in columns]
    if any(len(c) != n_rows for c in cols):
        raise ValueError("n_rows must equal the column length")
    out: list[str | None] = []
    for i in range(n_rows):
        if any(cols[c][i] is None for c, _ in slots):
            out.append(None)
            continue
        parts = [literals[0]]
        for k, (c, w) in enumerate(slots):
            parts.append(_fmt(cols[c][i], w))
            parts.append(literals[k + 1])
        out.append("".join(parts))
    return arrow_array(out, type=pa.string())


def compose_strings(
    literals: Sequence[str], pieces: Sequence[tuple[Any, ...]], row_start: int, n_rows: int
) -> pa.Array:
    """``literals[0] + piece + literals[1] + ...``; a piece is ``("pool", pool, k0, k1)`` (an entry
    picked per row), ``("int", k0, k1, low, high, width, remap_from, remap_to)`` (an integer drawn
    per row) or ``("col", array, width, slug)`` (a column of the caller; ``slug``: ASCII lower
    case, spaces removed). A null in a piece makes the row null."""
    if len(literals) != len(pieces) + 1:
        raise ValueError("compose needs one more literal than pieces")
    columns: list[list[Any]] = []
    for piece in pieces:
        kind = piece[0]
        if kind == "pool":
            _, pool, k0, k1 = piece
            entries = _values(pool)
            if pa.types.is_integer(pool.type):
                raise ValueError("compose needs a string pool")
            if not entries:
                raise ValueError("compose needs a non-empty pool")
            w = _words(k0, k1, row_start, n_rows, 1)
            columns.append([entries[i] for i in _index(w, len(entries))])
        elif kind == "int":
            _, k0, k1, low, high, width, remap_from, remap_to = piece
            if high <= low:
                raise ValueError("an integer piece needs low < high")
            w = _words(k0, k1, row_start, n_rows, 1)
            drawn = [int(low + i) for i in _index(w, high - low)]
            if remap_from is not None and remap_to is not None:
                drawn = [remap_to if v == remap_from else v for v in drawn]
            columns.append([_fmt(v, width) for v in drawn])
        elif kind == "col":
            _, array, width, slug = piece
            values = _values(array)
            if len(values) != n_rows:
                raise ValueError("n_rows must equal the length of every column piece")
            if slug:
                if any(isinstance(v, str) and not v.isascii() for v in values):
                    raise ValueError("a slug column must be ASCII text")
                values = [None if v is None else v.lower().replace(" ", "") for v in values]
            columns.append([None if v is None else _fmt(v, width) for v in values])
        else:
            raise ValueError(f"unknown piece kind {kind!r}")
    out: list[str | None] = []
    for i in range(n_rows):
        row = [col[i] for col in columns]
        if any(v is None for v in row):
            out.append(None)
            continue
        parts = [literals[0]]
        for k, v in enumerate(row):
            parts.append(v)
            parts.append(literals[k + 1])
        out.append("".join(parts))
    return arrow_array(out, type=pa.string())


def join_strings(columns: Sequence[Any], sep: str, skip_nulls: bool = False) -> pa.Array:
    cols = [_values(c) for c in columns]
    n = len(cols[0]) if cols else 0
    if any(len(c) != n for c in cols):
        raise ValueError("all columns must have the same length")
    out: list[str | None] = []
    for i in range(n):
        row = [c[i] for c in cols]
        if not skip_nulls and any(v is None for v in row):
            out.append(None)
        else:
            out.append(sep.join(_fmt(v, 0) for v in row if v is not None))
    return arrow_array(out, type=pa.string())


def _title(s: str) -> str:
    out: list[str] = []
    prev = False
    for c in s:
        alnum = c.isalnum()
        out.append(c.upper() if alnum and not prev else c.lower())
        prev = alnum
    return "".join(out)


def string_case(array: Any, mode: str) -> pa.Array:
    fn = {"upper": str.upper, "lower": str.lower, "title": _title}.get(mode)
    if fn is None:
        raise ValueError(f"case mode must be upper, lower or title, got {mode!r}")
    out = [
        None if v is None else (str(v) if not isinstance(v, str) else fn(v)) for v in _values(array)
    ]
    return arrow_array(out, type=pa.string())


def uuid4_strings(k0: int, k1: int, row_start: int, n_rows: int) -> pa.Array:
    w = _words(k0, k1, row_start, n_rows, 2).astype("<u8")
    raw = w.view(np.uint8).reshape(n_rows, 16).copy()
    raw[:, 6] = (raw[:, 6] & 0x0F) | 0x40
    raw[:, 8] = (raw[:, 8] & 0x3F) | 0x80
    out = []
    for row in raw:
        h = row.tobytes().hex()
        out.append(f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}")
    return arrow_array(out, type=pa.string())


def random_strings(
    k0: int, k1: int, row_start: int, n_rows: int, length: int, alphabet: str
) -> pa.Array:
    if not alphabet:
        raise ValueError("random_chars needs a non-empty alphabet")
    if length == 0:
        return arrow_array([""] * n_rows, type=pa.string())
    chars = np.array(list(alphabet), dtype=object)
    w = _words(k0, k1, row_start, n_rows, length).reshape(n_rows, length)
    idx = _below(w, len(chars))
    return arrow_array(["".join(row) for row in chars[idx]], type=pa.string())


# ---------------------------------------------------------------- temporal


def day_weights(
    start_day: int,
    n_days: int,
    month_weights: Sequence[float],
    dow_weights: Sequence[float],
    per_bucket: bool = True,
) -> pa.Array:
    if len(month_weights) != 12 or len(dow_weights) != 7:
        raise ValueError("need 12 month weights and 7 day-of-week weights")
    days = np.arange(start_day, start_day + n_days, dtype=np.int64)
    months = days.astype("datetime64[D]").astype("datetime64[M]").astype(np.int64) % 12
    dows = (days + 3) % 7
    mw = np.asarray(month_weights, dtype=np.float64)
    dw = np.asarray(dow_weights, dtype=np.float64)
    base = mw[months] * dw[dows]
    if per_bucket:
        count = np.zeros((12, 7), dtype=np.float64)
        np.add.at(count, (months, dows), 1.0)
        base = base / count[months, dows]
    return arrow_array(base, type=pa.float64())


def _erf(x: float) -> float:
    ax = abs(x)
    if ax >= 6.0:
        return math.copysign(1.0, x)
    x2 = ax * ax
    term = ax
    total = ax
    n = 1.0
    while term > 1e-17 * total:
        term *= 2.0 * x2 / (2.0 * n + 1.0)
        total += term
        n += 1.0
        if n > 500.0:
            break
    r = _FRAC_2_SQRT_PI * math.exp(-x2) * total
    return -r if x < 0 else r


def _cdf(z: float) -> float:
    return 0.5 * (1.0 + _erf(z / _SQRT_2))


def hour_weights_peaks(peaks: Sequence[float], std: float) -> pa.Array:
    if len(peaks) == 0 or not (math.isfinite(std) and std > 0.0):
        raise ValueError("hour_weights_peaks needs peaks and a positive std")
    k = math.ceil(8.0 * std / 24.0) + 1
    w = [0.0] * 24
    for h in range(24):
        for p in peaks:
            for j in range(-k, k + 1):
                lo = h + 24.0 * j - p
                w[h] += _cdf((lo + 1.0) / std) - _cdf(lo / std)
    return arrow_array(w, type=pa.float64())


def temporal_sample(
    day_weights: Any,
    hour_weights: Any,
    start_day: int,
    k0: int,
    k1: int,
    row_start: int,
    n_rows: int,
    whole_seconds: bool = False,
) -> pa.Array:
    dw = _f64(day_weights, "day_weights")
    hw = _f64(hour_weights, "hour_weights")
    if len(hw) != 24:
        raise ValueError("hour_weights must have 24 entries")
    dp, da = _build_alias([float(x) for x in dw])
    hp, ha = _build_alias([float(x) for x in hw])
    w = _words(k0, k1, row_start, n_rows, 5).reshape(n_rows, 5)
    day = _pick(np.asarray(dp), np.asarray(da, dtype=np.int64), w[:, 0], w[:, 1])
    hour = _pick(np.asarray(hp), np.asarray(ha, dtype=np.int64), w[:, 2], w[:, 3])
    if whole_seconds:
        within = _below(w[:, 4], 3600) * 1_000_000
    else:
        within = _below(w[:, 4], _US_PER_HOUR)
    micros = (start_day + day) * _US_PER_DAY + hour * _US_PER_HOUR + within
    return arrow_array(micros.astype("datetime64[us]"), type=pa.timestamp("us"))
