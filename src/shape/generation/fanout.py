"""Skewed foreign-key fan-out: the 80/20 helper.

Real child tables are lopsided: a few parents own most of the children. :class:`FanOut` draws a
parent index for every child row so that the top ``top_fraction`` of parents hold
``top_share`` of the children (the 80/20 rule is ``top_fraction=0.2, top_share=0.8``).

Two shapes: ``power`` (default) gives parent ``i`` of the ranking weight ``(i + 1) ** -a`` with
``a`` solved so the share is exact, a smooth long tail; ``two_tier`` gives every top parent the
same weight and every other parent the same smaller weight. Draws are alias samples from a
stream (two words per row), so a child's parent depends on its own row only, however the table
is chunked; ``shuffle`` (default) assigns the ranks to parents by a seeded permutation so the
heavy parents are not simply the first keys.

Stable interface: ``concentration_weights``, ``FanOut`` and ``top_share_of``.
"""

from __future__ import annotations

import math
from functools import lru_cache
from typing import Any

import numpy as np
import numpy.typing as npt

from shape.kernel import pmath

from . import kernel_ops
from .rng import RowStream

Floats = npt.NDArray[np.float64]
SHAPES = ("power", "two_tier")


def _check(n: int, top_fraction: float, top_share: float) -> int:
    if n < 1:
        raise ValueError("fan-out needs at least one parent")
    if not 0.0 < top_fraction < 1.0:
        raise ValueError("top_fraction must be strictly between 0 and 1")
    if not top_fraction <= top_share < 1.0:
        raise ValueError("top_share must be at least top_fraction and below 1")
    k = max(1, min(n - 1, round(n * top_fraction))) if n > 1 else 1
    if n > 1 and top_share < k / n:  # equal weights already give the top k parents k / n
        raise ValueError(
            f"top_share {top_share} is out of reach: the top {k} of {n} parents "
            f"(top_fraction {top_fraction}, rounded) hold at least {k / n:.4g} of the total"
        )
    return k


def concentration_weights(
    n: int, top_fraction: float = 0.2, top_share: float = 0.8, shape: str = "power"
) -> Floats:
    """Relative weights of ``n`` parents in rank order (heaviest first) such that the first
    ``round(n * top_fraction)`` of them hold ``top_share`` of the total."""
    if shape not in SHAPES:
        raise ValueError(f"shape must be one of {', '.join(SHAPES)}")
    k = _check(n, top_fraction, top_share)
    if n == 1:
        return np.ones(1)
    if shape == "two_tier":
        w = np.full(n, (1.0 - top_share) / (n - k))
        w[:k] = top_share / k
        return w
    ranks = np.arange(1, n + 1, dtype=np.float64)
    ln = pmath.log(ranks)

    def share(a: float) -> float:
        w = pmath.exp(-a * ln)
        return float(w[:k].sum() / w.sum())

    lo, hi = 0.0, 64.0  # share increases with the exponent
    if share(hi) < top_share:
        raise ValueError("top_share is out of reach for this many parents")
    for _ in range(100):
        mid = 0.5 * (lo + hi)
        lo, hi = (mid, hi) if share(mid) < top_share else (lo, mid)
    return pmath.exp(-0.5 * (lo + hi) * ln)


@lru_cache(maxsize=8)
def _permutation(key: int, n: int) -> npt.NDArray[np.int64]:
    perm: npt.NDArray[np.int64] = np.random.Generator(np.random.Philox(key=key)).permutation(n)
    perm.flags.writeable = False
    return perm


class FanOut:
    """A fixed fan-out over ``n_parents`` parents (indices ``0 .. n_parents - 1``)."""

    def __init__(
        self,
        n_parents: int,
        top_fraction: float = 0.2,
        top_share: float = 0.8,
        shape: str = "power",
        shuffle: bool = True,
    ) -> None:
        self.n_parents = n_parents
        self.top_fraction = top_fraction
        self.top_share = top_share
        self.shape = shape
        self.shuffle = shuffle
        self.weights = concentration_weights(n_parents, top_fraction, top_share, shape)
        self._table = kernel_ops.alias_table(self.weights.tolist())

    @classmethod
    def from_spec(cls, n_parents: int, spec: dict[str, Any]) -> FanOut:
        """From a ``fan_out`` spec such as ``{"top_fraction": 0.2, "top_share": 0.8}`` (keys
        ``top_fraction``, ``top_share``, ``shape``, ``shuffle``)."""
        return cls(
            n_parents,
            float(spec.get("top_fraction", 0.2)),
            float(spec.get("top_share", 0.8)),
            str(spec.get("shape", "power")),
            bool(spec.get("shuffle", True)),
        )

    def draw(self, stream: RowStream, row_start: int, n_rows: int) -> npt.NDArray[np.int64]:
        """The parent index (``0 .. n_parents - 1``) of rows ``row_start ..``."""
        rank = kernel_ops.alias_draw(self._table, stream, row_start, n_rows)
        if not self.shuffle:
            return rank
        return _permutation(stream.key, self.n_parents)[rank]


def top_share_of(
    parents: npt.NDArray[np.integer[Any]], n_parents: int, fraction: float = 0.2
) -> float:
    """The share of ``parents`` (one index per child) held by the ``fraction`` of parents with the
    most children: how skewed a generated column really is."""
    counts = np.sort(np.bincount(parents, minlength=n_parents))[::-1]
    k = max(1, math.ceil(n_parents * fraction))
    return float(counts[:k].sum() / counts.sum())
