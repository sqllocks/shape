"""Composable deterministic generation strategies.

These cover the useful semantic surface identified in the Spindle audit while
remaining independent of Spindle's API.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any


class Strategy:
    def generate(self, row: int, ctx: Mapping[str, Any], rng: random.Random) -> Any:
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class Constant(Strategy):
    value: Any

    def generate(self, row, ctx, rng):
        return self.value


@dataclass(frozen=True, slots=True)
class SequenceStrategy(Strategy):
    start: int = 1
    step: int = 1

    def generate(self, row, ctx, rng):
        return self.start + row * self.step


@dataclass(frozen=True, slots=True)
class Choice(Strategy):
    values: tuple[Any, ...]
    weights: tuple[float, ...] | None = None

    def __post_init__(self):
        if not self.values:
            raise ValueError("values cannot be empty")
        if self.weights is not None:
            if len(self.weights) != len(self.values):
                raise ValueError("weights length mismatch")
            if any(w < 0 for w in self.weights) or sum(self.weights) <= 0:
                raise ValueError("invalid weights")

    def generate(self, row, ctx, rng):
        return rng.choices(self.values, weights=self.weights, k=1)[0]


@dataclass(frozen=True, slots=True)
class Uniform(Strategy):
    low: float
    high: float

    def generate(self, row, ctx, rng):
        return rng.uniform(self.low, self.high)


@dataclass(frozen=True, slots=True)
class Normal(Strategy):
    mean: float
    stddev: float

    def generate(self, row, ctx, rng):
        return rng.gauss(self.mean, self.stddev)


@dataclass(frozen=True, slots=True)
class Conditional(Strategy):
    predicate: Callable[[Mapping[str, Any]], bool]
    when_true: Strategy
    when_false: Strategy

    def generate(self, row, ctx, rng):
        return (self.when_true if self.predicate(ctx) else self.when_false).generate(row, ctx, rng)


@dataclass(frozen=True, slots=True)
class Derived(Strategy):
    fn: Callable[[Mapping[str, Any]], Any]

    def generate(self, row, ctx, rng):
        return self.fn(ctx)


@dataclass(frozen=True, slots=True)
class ForeignKey(Strategy):
    values: tuple[Any, ...]

    def generate(self, row, ctx, rng):
        if not self.values:
            raise ValueError("foreign key domain is empty")
        return self.values[rng.randrange(len(self.values))]


@dataclass(frozen=True, slots=True)
class FirstPerParent(Strategy):
    parent_field: str
    first: Any
    subsequent: Any

    def generate(self, row, ctx, rng):
        seen = ctx.get("__seen_parents__")
        if seen is None:
            raise ValueError("FirstPerParent requires stateful plan context")
        parent = ctx[self.parent_field]
        if parent not in seen:
            seen.add(parent)
            return self.first
        return self.subsequent


@dataclass(frozen=True, slots=True)
class GenerationPlan:
    fields: tuple[tuple[str, Strategy], ...]
    seed: int = 0

    def row_at(self, i: int, seen=None):
        if i < 0:
            raise ValueError("row index must be non-negative")
        rng = random.Random((self.seed << 64) ^ i)
        state = set() if seen is None else seen
        row = {"__seen_parents__": state}
        for name, strategy in self.fields:
            row[name] = strategy.generate(i, row, rng)
        row.pop("__seen_parents__", None)
        return row

    def rows_at(self, indices):
        seen = set()
        for i in indices:
            yield self.row_at(int(i), seen)

    def rows(self, count: int):
        if count < 0:
            raise ValueError("count must be non-negative")
        seen = set()
        for i in range(count):
            yield self.row_at(i, seen)
