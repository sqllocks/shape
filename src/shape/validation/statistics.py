"""Empirical validation helpers."""

import random
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ValidationResult:
    passed: bool
    metric: str
    observed: float
    limit: float


def relative_error(observed, expected):
    return abs(observed - expected) / abs(expected) if expected else abs(observed)


def validate_cardinality(
    sketch_factory, cardinalities=(10, 100, 1000, 10000), trials=10, max_mean_relative_error=0.03
):
    errs = []
    for n in cardinalities:
        for t in range(trials):
            s = sketch_factory()
            vals = list(range(n))
            random.Random(t).shuffle(vals)
            for x in vals:
                s.update(x)
            errs.append(relative_error(s.estimate(), n))
    m = sum(errs) / len(errs)
    return ValidationResult(
        m <= max_mean_relative_error, "mean_relative_error", m, max_mean_relative_error
    )


def validate_quantiles(sketch_factory, n=20000, trials=5, max_rank_error=0.03):
    errs = []
    for t in range(trials):
        rng = random.Random(t)
        vals = [rng.random() ** 3 for _ in range(n)]
        s = sketch_factory()
        for x in vals:
            s.update(x)
        ordered = sorted(vals)
        for q in (0.01, 0.1, 0.5, 0.9, 0.99):
            est = s.quantile(q)
            rank = sum(v <= est for v in ordered) / n
            errs.append(abs(rank - q))
    w = max(errs)
    return ValidationResult(w <= max_rank_error, "worst_rank_error", w, max_rank_error)
