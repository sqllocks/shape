"""Tests for the empirical validation helpers and the correctness invariants (AUD-quality)."""

from __future__ import annotations

import math

from shape.generation.strategies import GenerationPlan, SequenceStrategy
from shape.validation import relative_error, validate_cardinality, validate_quantiles
from shape.validation.invariants import (
    deterministic_partitions,
    finite_shape,
    referential_integrity,
    uniqueness,
)


class _ExactCount:
    def __init__(self) -> None:
        self.seen: set[int] = set()

    def update(self, x: int) -> None:
        self.seen.add(x)

    def estimate(self) -> float:
        return float(len(self.seen))


class _HalfCount(_ExactCount):
    def estimate(self) -> float:
        return len(self.seen) / 2


class _ExactQuantiles:
    def __init__(self) -> None:
        self.values: list[float] = []

    def update(self, x: float) -> None:
        self.values.append(x)

    def quantile(self, q: float) -> float:
        ordered = sorted(self.values)
        return ordered[min(int(q * len(ordered)), len(ordered) - 1)]


def test_relative_error():
    assert relative_error(110, 100) == 0.1
    assert relative_error(3, 0) == 3


def test_validate_cardinality_passes_an_exact_counter_and_fails_a_biased_one():
    good = validate_cardinality(_ExactCount, cardinalities=(10, 100), trials=2)
    assert good.passed and good.observed == 0.0 and good.metric == "mean_relative_error"
    bad = validate_cardinality(_HalfCount, cardinalities=(10, 100), trials=2)
    assert not bad.passed and math.isclose(bad.observed, 0.5)


def test_validate_quantiles_passes_exact_quantiles():
    result = validate_quantiles(_ExactQuantiles, n=500, trials=2)
    assert result.passed and result.metric == "worst_rank_error"
    assert result.observed <= 0.01


def test_referential_integrity_and_uniqueness():
    parents = [{"id": 1}, {"id": 2}]
    children = [{"pid": 1}, {"pid": 3}, {"pid": 2}]
    assert referential_integrity(parents, children, "id", "pid") == {
        "passed": False,
        "violating_rows": (1,),
        "checked": 3,
    }
    rows = [{"a": 1, "b": 1}, {"a": 1, "b": 2}, {"a": 1, "b": 1}]
    assert uniqueness(rows, ["a", "b"])["violating_rows"] == (2,)
    assert uniqueness(rows, ["a"])["violating_rows"] == (1, 2)


def test_deterministic_partitions():
    plan = GenerationPlan((("id", SequenceStrategy()),), 7)
    assert deterministic_partitions(plan, 6, [(0, 2), (2, 6)])
    assert not deterministic_partitions(plan, 6, [(0, 3)])


def test_finite_shape():
    assert finite_shape({"a": [1.0, {"b": (2.0, "x")}]})
    assert not finite_shape({"a": [1.0, {"b": (float("nan"),)}]})
    assert not finite_shape([float("inf")])
