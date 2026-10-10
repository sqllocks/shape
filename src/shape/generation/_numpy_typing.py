"""Narrow contracts for numeric operations missing from NumPy's scalar stubs."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    import numpy as np


class Float64Ratio(Protocol):
    """Division of int64 or float64 scalars keeps both operands unchanged."""

    def __truediv__(self, other: np.integer[Any] | np.floating[Any], /) -> np.float64: ...
