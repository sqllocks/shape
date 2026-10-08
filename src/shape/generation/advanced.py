"""Higher-fidelity generation strategies."""

from __future__ import annotations

import math
from bisect import bisect_left
from dataclasses import dataclass

from .strategies import Strategy


@dataclass(frozen=True, slots=True)
class Empirical(Strategy):
    values: tuple
    cumulative: tuple[float, ...]

    def __post_init__(self):
        if not self.values or len(self.values) != len(self.cumulative):
            raise ValueError("invalid empirical distribution")
        if any(
            self.cumulative[i] <= (self.cumulative[i - 1] if i else 0)
            for i in range(len(self.cumulative))
        ):
            raise ValueError("cumulative probabilities must increase")
        if abs(self.cumulative[-1] - 1.0) > 1e-9:
            raise ValueError("cumulative distribution must end at 1")

    def generate(self, row, ctx, rng):
        return self.values[min(bisect_left(self.cumulative, rng.random()), len(self.values) - 1)]


@dataclass(frozen=True, slots=True)
class CorrelatedNormal(Strategy):
    source_field: str
    source_mean: float
    source_stddev: float
    target_mean: float
    target_stddev: float
    rho: float

    def __post_init__(self):
        if self.source_stddev <= 0 or self.target_stddev < 0 or not -1 <= self.rho <= 1:
            raise ValueError("invalid correlation parameters")

    def generate(self, row, ctx, rng):
        x = (float(ctx[self.source_field]) - self.source_mean) / self.source_stddev
        z = rng.gauss(0, 1)
        return self.target_mean + self.target_stddev * (
            self.rho * x + math.sqrt(max(0, 1 - self.rho * self.rho)) * z
        )


@dataclass(frozen=True, slots=True)
class UniqueToken(Strategy):
    prefix: str = ""
    width: int = 0

    def generate(self, row, ctx, rng):
        x = str(row)
        return self.prefix + (x.zfill(self.width) if self.width else x)
