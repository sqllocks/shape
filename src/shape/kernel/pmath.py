"""Portable math through the selected kernel (#768): the same bits on every CPU and platform.

Generation computes every logarithm, exponential, power and cosine that reaches a value with these
functions, never with ``numpy.log``/``exp``/``log1p``/``power``/``cos``, ``**`` on floats, ``math``
or ``numpy.interp``, whose last bits depend on the CPU (numpy's AVX-512 routines) and the C library
(Linux, macOS, Windows). :mod:`shape.kernel.reference.pmath` documents the algorithms; the native
kernel computes ``log``, ``exp``, ``pow`` and ``cos_turns`` with the same operations (faster, the
same bits), and ``log1p``, ``lgamma`` and ``interp`` are numpy in both kernel modes.

Each function takes an array or a number and returns a float64 array of the input's shape
(``float(...)`` of a 0-d result for a number).
"""

from __future__ import annotations

from typing import Any

import numpy as np
import numpy.typing as npt

from shape.generation.arrowkit import array as arrow_array
from shape.generation.arrowkit import to_numpy as arrow_numpy

from .dispatch import get_kernel
from .reference import pmath as _ref

Floats = npt.NDArray[np.float64]

# Below this many elements the numpy twin is used directly (the same bits; no Arrow round trip).
_NATIVE_FROM = 64


def _call(name: str, x: Any, *args: float) -> Floats:
    a = np.asarray(x, dtype=np.float64)
    flat = np.ascontiguousarray(a.reshape(-1))
    kernel = get_kernel()
    if kernel.NAME == "python" or flat.size < _NATIVE_FROM:
        out: Floats = getattr(_ref, name)(flat, *args)
    else:
        result = getattr(kernel, f"pm_{name}")(arrow_array(flat), *args)
        out = np.asarray(arrow_numpy(arrow_array(result)), dtype=np.float64)
    return out.reshape(a.shape)


def log(x: Any) -> Floats:
    """Natural logarithm (``nan`` below 0, ``-inf`` at 0)."""
    return _call("log", x)


def exp(x: Any) -> Floats:
    """``e ** x``."""
    return _call("exp", x)


def pow(x: Any, y: float) -> Floats:
    """``x ** y`` for ``x >= 0`` and one exponent ``y`` (``nan`` for ``x < 0``)."""
    return _call("pow", x, float(y))


def cos_turns(t: Any) -> Floats:
    """``cos(2 * pi * t)``, with ``t`` in turns."""
    return _call("cos_turns", t)


def log1p(x: Any) -> Floats:
    """``log(1 + x)``, accurate for small ``x``."""
    a = np.asarray(x, dtype=np.float64)
    return _ref.log1p(a.reshape(-1)).reshape(a.shape)


def lgamma(x: Any) -> Floats:
    """``ln Gamma(x)`` for ``x > 0``."""
    a = np.asarray(x, dtype=np.float64)
    return _ref.lgamma(a.reshape(-1)).reshape(a.shape)


def interp(x: Any, xp: Any, fp: Any) -> Floats:
    """``numpy.interp`` for increasing ``xp``, without a fused multiply-add on any machine."""
    a = np.asarray(x, dtype=np.float64)
    return _ref.interp(a.reshape(-1), xp, fp).reshape(a.shape)


__all__ = ["cos_turns", "exp", "interp", "lgamma", "log", "log1p", "pow"]
