"""Special functions the family fits need, in numpy (no scipy): digamma and trigamma.

Both shift the argument above 6 with the recurrence and then use the asymptotic series, which is
accurate to better than 1e-12 there.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import numpy.typing as npt

Floats = npt.NDArray[np.float64]


def _shifted(x: Any) -> tuple[Floats, Floats, Floats]:
    """(x raised to at least 6, sum of 1/(x+j), sum of 1/(x+j)^2 over the shift)."""
    x = np.array(x, dtype=np.float64)
    if (x <= 0).any():
        raise ValueError("digamma and trigamma need positive arguments")
    s1 = np.zeros_like(x)
    s2 = np.zeros_like(x)
    for _ in range(6):
        low = x < 6.0
        s1 = np.where(low, s1 + 1.0 / x, s1)
        s2 = np.where(low, s2 + 1.0 / (x * x), s2)
        x = np.where(low, x + 1.0, x)
    return x, s1, s2


def digamma(x: Any) -> Floats:
    """psi(x) = d/dx ln Gamma(x), for x > 0."""
    z, s1, _ = _shifted(x)
    inv2 = 1.0 / (z * z)
    series = inv2 * (
        1 / 12 - inv2 * (1 / 120 - inv2 * (1 / 252 - inv2 * (1 / 240 - inv2 * (1 / 132))))
    )
    out: Floats = np.log(z) - 0.5 / z - series - s1
    return out


def trigamma(x: Any) -> Floats:
    """psi'(x), for x > 0."""
    z, _, s2 = _shifted(x)
    inv = 1.0 / z
    inv2 = inv * inv
    series = (
        inv
        + 0.5 * inv2
        + inv
        * inv2
        * (1 / 6 - inv2 * (1 / 30 - inv2 * (1 / 42 - inv2 * (1 / 30 - inv2 * (5 / 66)))))
    )
    out: Floats = series + s2
    return out
