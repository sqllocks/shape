"""Portable math: ``log``, ``exp``, ``pow``, ``log1p``, ``cos`` of turns, ``lgamma`` and ``interp``
with the same bits on every CPU and operating system (#768).

numpy computes float64 ``log``, ``exp``, ``log1p``, ``power`` and ``cos`` with CPU-dispatched code
(AVX-512 routines on some x86 CPUs, the C library elsewhere), and the C libraries of Linux, macOS
and Windows differ too, so the last bits of a value depend on the machine. Generation must not:
a pinned spec gives the same dataset id and the same file bytes everywhere
(``docs/GENERATION_STABILITY.md``). The functions here are written with IEEE 754 basic operations
only (``+ - * /``, ``sqrt``, comparisons, truncation and rounding to an integer, and exponent
arithmetic on the bit pattern), each a separate numpy operation, so nothing can be fused or
dispatched differently. They follow fdlibm's algorithms (``e_log.c``, ``e_exp.c``, ``k_sin.c``,
``k_cos.c``) with its constants, which are given here by their bit patterns. Their error is below
one unit in the last place for ``log``, ``exp`` and ``cos_turns`` (a test checks it against the C
library); ``pow(x, y)`` is ``exp(y * log(x))``, within about ``1 + |y * ln x|`` units (exact or
correctly rounded for ``y`` = 1, 2, 0.5 and -1, which take one basic operation).

The native kernel (``rust/shape-kernel/src/gen/pmath.rs``) has the same functions with the same
operations in the same order, so the two kernels agree bit for bit (``pm_log``, ``pm_exp``,
``pm_pow``, ``pm_cos_turns``). ``lgamma``, ``log1p`` and ``interp`` are numpy only: callers use
them for tables, the same in both kernel modes. ``exp_scalar`` is ``exp`` for one Python float, in
plain Python arithmetic (for the twin of the kernel's ``erf``).
"""

from __future__ import annotations

import math
import struct
from typing import Any

import numpy as np
import numpy.typing as npt
import pyarrow as pa  # type: ignore[import-untyped]

from shape.generation.arrowkit import array as arrow_array
from shape.generation.arrowkit import to_numpy as arrow_numpy

Floats = npt.NDArray[np.float64]


def _f(bits: int) -> float:
    value: float = struct.unpack("<d", bits.to_bytes(8, "little"))[0]
    return value


# log (fdlibm e_log.c)
LN2_HI = _f(0x3FE62E42FEE00000)
LN2_LO = _f(0x3DEA39EF35793C76)
LG1 = _f(0x3FE5555555555593)
LG2 = _f(0x3FD999999997FA04)
LG3 = _f(0x3FD2492494229359)
LG4 = _f(0x3FCC71C51D8E78AF)
LG5 = _f(0x3FC7466496CB03DE)
LG6 = _f(0x3FC39A09D078C69F)
LG7 = _f(0x3FC2F112DF3E5244)
SQRT2_SPLIT = _f(0x3FF6A09C00000000)  # mantissa at or above it: use m / 2 (fdlibm 0x6a09c)
HFSQ_LOW = _f(0x3FF6147A00000000)  # mantissas in [HFSQ_LOW, HFSQ_HIGH) take the hfsq form
HFSQ_HIGH = _f(0x3FF6B85200000000)
TWO54 = _f(0x4350000000000000)
TINY = _f(0x0010000000000000)  # 2 ** -1022, the smallest normal number

# exp (fdlibm e_exp.c)
O_THRESHOLD = _f(0x40862E42FEFA39EF)
U_THRESHOLD = _f(0xC0874910D52D3051)
INVLN2 = _f(0x3FF71547652B82FE)
P1 = _f(0x3FC555555555553E)
P2 = _f(0xBF66C16C16BEBD93)
P3 = _f(0x3F11566AAF25DE2C)
P4 = _f(0xBEBBBD41C5D26BF1)
P5 = _f(0x3E66376972BEA4D0)
TWOM1000 = _f(0x0170000000000000)  # 2 ** -1000

# sin and cos on [-pi/4, pi/4] (fdlibm k_sin.c, k_cos.c)
S1 = _f(0xBFC5555555555549)
S2 = _f(0x3F8111111110F8A6)
S3 = _f(0xBF2A01A019C161D5)
S4 = _f(0x3EC71DE357B1FE7D)
S5 = _f(0xBE5AE5E68A2B9CEB)
S6 = _f(0x3DE5D93A5ACFD57C)
C1 = _f(0x3FA555555555554C)
C2 = _f(0xBF56C16C16C15177)
C3 = _f(0x3EFA01A019CB1590)
C4 = _f(0xBE927E4F809C52AD)
C5 = _f(0x3E21EE9EBDB4B1C4)
C6 = _f(0xBDA8FAE9BE8838D4)
TWO_PI = _f(0x401921FB54442D18)

# lgamma: Stirling's series from 8 up
HALF_LN_2PI = 0.9189385332046727418  # ln(2 pi) / 2
_STIRLING = (
    1.0 / 12.0,
    -1.0 / 360.0,
    1.0 / 1260.0,
    -1.0 / 1680.0,
    1.0 / 1188.0,
    -691.0 / 360360.0,
    1.0 / 156.0,
)

_EXP_MASK = 0x7FF
_MANT_MASK = 0x000FFFFFFFFFFFFF
_ONE_BITS = 0x3FF0000000000000


def _arr(x: Any) -> Floats:
    out: Floats = np.array(x, dtype=np.float64, copy=True, order="C", ndmin=1)
    return out


def log(x: Any) -> Floats:
    """Natural logarithm, elementwise (``nan`` below 0, ``-inf`` at 0)."""
    x = _arr(x)
    with np.errstate(all="ignore"):
        sub = (x > 0.0) & (x < TINY)
        xs = np.where(sub, x * TWO54, x)
        b = xs.view(np.int64)
        k = ((b >> 52) & _EXP_MASK) - 1023 - np.where(sub, 54, 0)
        m = ((b & _MANT_MASK) | _ONE_BITS).view(np.float64)
        hfsq_form = (m >= HFSQ_LOW) & (m < HFSQ_HIGH)
        up = m >= SQRT2_SPLIT
        m = np.where(up, m * 0.5, m)
        k = k + up
        f = m - 1.0
        s = f / (2.0 + f)
        dk = k.astype(np.float64)
        z = s * s
        w = z * z
        t1 = w * (LG2 + w * (LG4 + w * LG6))
        t2 = z * (LG1 + w * (LG3 + w * (LG5 + w * LG7)))
        r = t2 + t1
        hfsq = 0.5 * f * f
        a = dk * LN2_HI - ((hfsq - (s * (hfsq + r) + dk * LN2_LO)) - f)
        c = dk * LN2_HI - ((s * (f - r) - dk * LN2_LO) - f)
        out = np.where(hfsq_form, a, c)
    out = np.where(x == 0.0, -np.inf, out)
    out = np.where(x == np.inf, np.inf, out)
    out = np.where((x < 0.0) | np.isnan(x), np.nan, out)
    return out


def exp(x: Any) -> Floats:
    """``e ** x``, elementwise."""
    x = _arr(x)
    with np.errstate(all="ignore"):
        xc = np.where(np.isfinite(x), np.clip(x, -746.0, 710.0), 0.0)
        half = np.where(xc < 0.0, -0.5, 0.5)
        k = np.trunc(xc * INVLN2 + half)
        hi = xc - k * LN2_HI
        lo = k * LN2_LO
        r = hi - lo
        t = r * r
        c = r - t * (P1 + t * (P2 + t * (P3 + t * (P4 + t * P5))))
        y = 1.0 - ((lo - (r * c) / (2.0 - c)) - hi)
        ki = k.astype(np.int64)
        normal = ki >= -1021
        top = ki == 1024
        kk = np.where(top, 1023, np.where(normal, ki, ki + 1000))
        twopk = ((kk + 1023) << 52).view(np.float64)
        y = np.where(top, y * 2.0, y)
        out = y * twopk
        out = np.where(normal, out, out * TWOM1000)
    out = np.where(x > O_THRESHOLD, np.inf, out)
    out = np.where(x < U_THRESHOLD, 0.0, out)
    out = np.where(np.isnan(x), np.nan, out)
    return out


def pow(x: Any, y: float) -> Floats:
    """``x ** y`` for ``x >= 0`` (``nan`` below 0): ``x``, ``x * x``, ``sqrt(x)`` or ``1 / x`` for
    ``y`` = 1, 2, 0.5 or -1, else ``exp(y * log(x))``."""
    x = _arr(x)
    y = float(y)
    if y == 0.0:
        return np.where(np.isnan(x) | (x < 0.0), np.nan, 1.0)
    with np.errstate(all="ignore"):
        if y == 1.0:  # exponents with an exact (or correctly rounded) answer from one operation
            out = x.copy()
        elif y == 2.0:
            out = x * x
        elif y == 0.5:
            out = np.sqrt(x)
        elif y == -1.0:
            out = 1.0 / x
        else:
            out = exp(y * log(x))
    out = np.where(x == 1.0, 1.0, out)
    out = np.where(x == 0.0, 0.0 if y > 0.0 else np.inf, out)
    return np.where((x < 0.0) | np.isnan(x) | math.isnan(y), np.nan, out)


def _sin_k(x: Floats) -> Floats:
    z = x * x
    w = z * z
    r = S2 + z * (S3 + z * S4) + z * w * (S5 + z * S6)
    v = z * x
    out: Floats = x + v * (S1 + z * r)
    return out


def _cos_k(x: Floats) -> Floats:
    z = x * x
    w = z * z
    r = z * (C1 + z * (C2 + z * C3)) + w * w * (C4 + z * (C5 + z * C6))
    hz = 0.5 * z
    one_hz = 1.0 - hz
    out: Floats = one_hz + (((1.0 - one_hz) - hz) + z * r)
    return out


def cos_turns(t: Any) -> Floats:
    """``cos(2 * pi * t)``, elementwise: ``t`` in turns, reduced to an eighth of a turn exactly
    (``nan`` for infinite ``t``, and ``1`` for ``|t| >= 2 ** 50``, whole turns at that size)."""
    t = _arr(t)
    with np.errstate(all="ignore"):
        tc = np.where(np.isfinite(t) & (np.abs(t) < 2.0**50), t, 0.0)
        n = np.rint(4.0 * tc)
        r = tc - n * 0.25
        x = r * TWO_PI
        q = n.astype(np.int64) & 3
        s, c = _sin_k(x), _cos_k(x)
        out = np.where(q == 0, c, np.where(q == 1, -s, np.where(q == 2, -c, s)))
    return np.where(np.isfinite(t), out, np.nan)


def log1p(x: Any) -> Floats:
    """``log(1 + x)``, elementwise: ``log(u) * x / (u - 1)`` with ``u = 1 + x`` (Kahan's trick;
    ``x`` itself where ``u`` rounds to 1)."""
    x = _arr(x)
    u = 1.0 + x
    with np.errstate(all="ignore"):
        out = log(u) * x / (u - 1.0)
    out = np.where(u == 1.0, x, out)
    return np.where(u == np.inf, np.inf, out)


def lgamma(x: Any) -> Floats:
    """``ln Gamma(x)`` for ``x > 0`` (``nan`` elsewhere): shifted up to 8 or more, then Stirling's
    series to the ``z ** -13`` term (absolute error about 1e-15 times the result)."""
    x = _arr(x)
    bad = ~(x > 0.0) | np.isinf(x)
    z = np.where(bad, 8.0, x)
    prod = np.ones_like(z)
    for _ in range(8):
        low = z < 8.0
        prod = np.where(low, prod * z, prod)
        z = np.where(low, z + 1.0, z)
    inv = 1.0 / z
    inv2 = inv * inv
    series = _STIRLING[-1]
    for coef in reversed(_STIRLING[:-1]):
        series = coef + inv2 * series
    out = (z - 0.5) * log(z) - z + HALF_LN_2PI + inv * series - log(prod)
    out = np.where(np.isinf(x) & (x > 0), np.inf, out)
    return np.where(bad & ~(np.isinf(x) & (x > 0)), np.nan, out)


def interp(x: Any, xp: Any, fp: Any) -> Floats:
    """``numpy.interp(x, xp, fp)`` for increasing ``xp`` (``fp[0]`` below, ``fp[-1]`` above), with
    the slope and the line evaluated as separate operations, so no machine fuses them into one
    multiply-add."""
    x = _arr(x)
    xp = np.asarray(xp, dtype=np.float64)
    fp = np.asarray(fp, dtype=np.float64)
    if xp.ndim != 1 or xp.shape != fp.shape or len(xp) == 0:
        raise ValueError("interp needs xp and fp of the same non-zero length")
    if len(xp) == 1:
        return np.where(np.isnan(x), np.nan, np.full_like(x, fp[0]))
    j = np.clip(np.searchsorted(xp, x, side="right") - 1, 0, len(xp) - 2)
    x0, x1, y0, y1 = xp[j], xp[j + 1], fp[j], fp[j + 1]
    with np.errstate(all="ignore"):
        slope = (y1 - y0) / (x1 - x0)
        out = slope * (x - x0) + y0
        again = slope * (x - x1) + y1
    out = np.where(np.isnan(out), again, out)
    out = np.where(np.isnan(out) & (y0 == y1), y0, out)
    out = np.where(x == x0, y0, out)
    out = np.where(x >= xp[-1], fp[-1], out)
    out = np.where(x < xp[0], fp[0], out)
    return np.where(np.isnan(x), np.nan, out)


# ---- one Python float --------------------------------------------------------------------------


def _bits(x: float) -> int:
    value: int = struct.unpack("<q", struct.pack("<d", x))[0]
    return value


def exp_scalar(x: float) -> float:
    """:func:`exp` of one float, in plain Python arithmetic (the same operations and bits)."""
    if math.isnan(x):
        return math.nan
    if x > O_THRESHOLD:
        return math.inf
    if x < U_THRESHOLD:
        return 0.0
    k = float(math.trunc(x * INVLN2 + (-0.5 if x < 0.0 else 0.5)))
    hi = x - k * LN2_HI
    lo = k * LN2_LO
    r = hi - lo
    t = r * r
    c = r - t * (P1 + t * (P2 + t * (P3 + t * (P4 + t * P5))))
    y = 1.0 - ((lo - (r * c) / (2.0 - c)) - hi)
    ki = int(k)
    if ki == 1024:
        return y * 2.0 * _f(0x7FE0000000000000)
    if ki >= -1021:
        return y * _f((ki + 1023) << 52)
    return y * _f((ki + 1000 + 1023) << 52) * TWOM1000


# ---- the kernel functions (twins of gen/pmath.rs) ----------------------------------------------


def _values(a: Any, what: str) -> Floats:
    arr = a if isinstance(a, pa.Array) else arrow_array(a)
    if not pa.types.is_float64(arr.type):
        raise ValueError(f"{what} must be a float64 array")
    if arr.null_count:
        raise ValueError(f"{what} must not contain nulls")
    return np.asarray(arrow_numpy(arr), dtype=np.float64)


def pm_log(x: Any) -> pa.Array:
    """:func:`log` of a float64 array."""
    return arrow_array(log(_values(x, "x")))


def pm_exp(x: Any) -> pa.Array:
    """:func:`exp` of a float64 array."""
    return arrow_array(exp(_values(x, "x")))


def pm_pow(x: Any, y: float) -> pa.Array:
    """:func:`pow` of a float64 array and one exponent."""
    return arrow_array(pow(_values(x, "x"), y))


def pm_cos_turns(t: Any) -> pa.Array:
    """:func:`cos_turns` of a float64 array."""
    return arrow_array(cos_turns(_values(t, "t")))
