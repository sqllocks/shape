"""Composable deterministic generation strategies.

These cover the useful semantic surface of a synthetic-data generator and stay independent
of any other library's API.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any


class Strategy:
    """Base interface for a row-indexed generation strategy."""

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
    """``first`` for the first row of each ``parent_field`` value, ``subsequent`` for the rest.

    "First" means no earlier row has the same parent value, so the label of row ``i`` is a
    function of the plan and ``i`` alone: it does not depend on which rows were asked for before
    (plan Appendix A, G2). The plan answers through ``ctx["__first_row__"]``.
    """

    parent_field: str
    first: Any
    subsequent: Any

    def generate(self, row, ctx, rng):
        resolve = ctx.get("__first_row__")
        if resolve is None:
            raise ValueError("FirstPerParent must run inside a GenerationPlan")
        return self.first if resolve(self, ctx[self.parent_field]) == row else self.subsequent


@dataclass(frozen=True, slots=True)
class GenerationPlan:
    fields: tuple[tuple[str, Strategy], ...]
    seed: int = 0
    # Per FirstPerParent: parent value -> first row with it, and how many rows were scanned.
    # A cache only: every entry is a function of (fields, seed), never of the order of calls.
    _first_rows: dict[int, dict[Any, int]] = field(default_factory=dict, compare=False, repr=False)
    _scanned: dict[int, int] = field(default_factory=dict, compare=False, repr=False)

    def _eval_row(self, i: int, stop_at: str | None = None) -> dict[str, Any]:
        # random.Random seeds an int with abs(): a negative seed is keyed as text instead
        rng = random.Random((self.seed << 64) ^ i if self.seed >= 0 else f"{self.seed}:{i}")
        row: dict[str, Any] = {"__first_row__": self._first_row_of(i)}
        for name, strategy in self.fields:
            row[name] = strategy.generate(i, row, rng)
            if name == stop_at:
                break
        return row

    def _first_row_of(self, i: int):
        def first_row(strategy: FirstPerParent, parent: Any) -> int:
            key = id(strategy)
            firsts = self._first_rows.setdefault(key, {})
            done = self._scanned.get(key, 0)
            for j in range(done, i + 1):
                # Rows before ``i`` are only read up to the parent field, which never needs
                # this strategy's own answer for the same row.
                firsts.setdefault(
                    self._eval_row(j, strategy.parent_field)[strategy.parent_field], j
                )
            self._scanned[key] = max(done, i + 1)
            return firsts[parent]

        return first_row

    def row_at(self, i: int, seen=None):
        """Row ``i``. ``seen`` is accepted for compatibility and ignored: the answer never
        depends on what was generated before."""
        if i < 0:
            raise ValueError("row index must be non-negative")
        row = self._eval_row(i)
        row.pop("__first_row__", None)
        return row

    def rows_at(self, indices):
        for i in indices:
            yield self.row_at(int(i))

    def rows(self, count: int):
        if count < 0:
            raise ValueError("count must be non-negative")
        for i in range(count):
            yield self.row_at(i)
